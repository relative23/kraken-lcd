"""Wiring tests for the command line interface (exit codes, commands)."""

import shutil

import pytest

from kraken_lcd import cli
from kraken_lcd.device import (DeviceInBootloader, DeviceNotFound, DeviceStatus,
                               KrakenDevice)


@pytest.fixture
def project(tmp_path, tiny_gif):
    """Minimal self-contained project config for CLI runs."""
    assets = tmp_path / "assets"
    assets.mkdir()
    for name in ("cpu.gif", "temp.gif"):
        shutil.copy(tiny_gif, assets / name)
    config = tmp_path / "config.toml"
    config.write_text(
        f'[carousel]\nscreens = ["cpu", "temps"]\n'
        f'[render]\nsize = 96\nmax_frames = 2\ncolors = 16\n'
        f'assets_dir = "{assets}"\n'
        f'[cache]\ndir = "{tmp_path / "cache"}"\n')
    return config


def test_render_command_end_to_end(project, tmp_path, capsys):
    out_dir = tmp_path / "preview"
    code = cli.main(["--config", str(project), "render", "--out", str(out_dir)])
    assert code == 0
    assert (out_dir / "cpu.gif").stat().st_size > 0
    assert "cpu:" in capsys.readouterr().out


def test_render_returns_error_when_nothing_renders(project, tmp_path, monkeypatch):
    from kraken_lcd.sensors import SensorSnapshot

    class NoSensors:
        def snapshot(self, **kwargs):
            return SensorSnapshot()  # everything unavailable

    monkeypatch.setattr(cli, "SensorReader", NoSensors)
    code = cli.main(["--config", str(project), "render",
                     "--out", str(tmp_path / "empty")])
    assert code == 1


def test_missing_config_file_returns_2(tmp_path):
    assert cli.main(["--config", str(tmp_path / "nope.toml"), "status"]) == 2


def test_invalid_config_value_returns_2(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text("[carousel]\nbrightness = 500\n")
    assert cli.main(["--config", str(config), "status"]) == 2


def test_command_is_required():
    with pytest.raises(SystemExit):
        cli.main([])


class _UnreachableDevice:
    def __init__(self, cfg):
        pass

    def connect(self):
        raise DeviceNotFound("no device")

    def disconnect(self):
        pass


def test_status_degrades_without_device(project, monkeypatch, capsys):
    monkeypatch.setattr(cli, "KrakenDevice", _UnreachableDevice)
    assert cli.main(["--config", str(project), "status"]) == 0
    out = capsys.readouterr().out
    assert "not reachable" in out
    assert "CPU load" in out


def test_status_reports_device_values(project, monkeypatch, capsys):
    class HappyDevice(_UnreachableDevice):
        description = "NZXT Kraken 2024 Elite RGB"

        def connect(self):
            pass

        def read_status(self):
            return DeviceStatus(liquid_temp=41.5, pump_rpm=1850, fan_rpm=900)

    monkeypatch.setattr(cli, "KrakenDevice", HappyDevice)
    assert cli.main(["--config", str(project), "status"]) == 0
    out = capsys.readouterr().out
    assert "41.5 °C" in out
    assert "1850 rpm" in out


def test_reset_command(project, monkeypatch, capsys):
    resets = []

    class ResettableDevice(_UnreachableDevice):
        def connect(self):
            pass

        def reset_to_liquid(self):
            resets.append(1)

    monkeypatch.setattr(cli, "KrakenDevice", ResettableDevice)
    assert cli.main(["--config", str(project), "reset"]) == 0
    assert resets == [1]


def test_reset_without_device_returns_1(project, monkeypatch):
    monkeypatch.setattr(cli, "KrakenDevice", _UnreachableDevice)
    assert cli.main(["--config", str(project), "reset"]) == 1


class _FakeCarousel:
    exception: Exception | None = None

    def __init__(self, cfg, device, sensors, cache):
        pass

    def install_signal_handlers(self):
        pass

    def run(self):
        if self.exception is not None:
            raise self.exception


@pytest.fixture
def fake_carousel(project, monkeypatch):
    monkeypatch.setattr(cli, "Carousel", _FakeCarousel)
    monkeypatch.setattr(cli, "KrakenDevice", lambda cfg: None)
    yield _FakeCarousel
    _FakeCarousel.exception = None


def test_run_returns_0_on_clean_stop(project, fake_carousel):
    assert cli.main(["--config", str(project), "run"]) == 0


def test_run_returns_78_for_bootloader(project, fake_carousel):
    fake_carousel.exception = DeviceInBootloader("wedged")
    assert cli.main(["--config", str(project), "run"]) == 78


def test_run_returns_1_for_device_errors(project, fake_carousel):
    fake_carousel.exception = DeviceNotFound("gone")
    assert cli.main(["--config", str(project), "run"]) == 1


def test_run_returns_1_for_unexpected_errors(project, fake_carousel):
    fake_carousel.exception = ValueError("boom")
    assert cli.main(["--config", str(project), "run"]) == 1


def test_default_finder_respects_driver_patch_config(monkeypatch):
    seen = []
    monkeypatch.setattr("kraken_lcd.device._find_kraken",
                        lambda use_patch: seen.append(use_patch))
    from kraken_lcd.config import DeviceConfig
    KrakenDevice(DeviceConfig(driver_patch=False))._finder()
    KrakenDevice(DeviceConfig(driver_patch=True))._finder()
    assert seen == [False, True]
