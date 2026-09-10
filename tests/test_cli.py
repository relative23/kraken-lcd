"""Wiring tests for the command line interface (exit codes, commands)."""

import shutil

import pytest

from kraken_lcd import cli, upstream
from kraken_lcd.device import (
    DeviceInBootloader,
    DeviceNotFound,
    DeviceStatus,
    DeviceUnsupported,
    KrakenDevice,
)


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


def test_run_returns_78_for_unsupported_firmware(project, fake_carousel):
    # a restart cannot fix a firmware that refuses GIFs; same no-restart
    # exit status as the bootloader case
    fake_carousel.exception = DeviceUnsupported("firmware 2.x cannot show GIFs")
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


# ------------------------------------------------------------------ doctor

_COMPATIBLE = upstream.Compatibility("1.15.0", (), verified_release=True)
_INCOMPATIBLE = upstream.Compatibility(
    "1.99.0", ("KrakenZ3._send_data changed upstream (fingerprint x, expected y)",))


class _DoctorDevice(_UnreachableDevice):
    description = "NZXT Kraken 2024 Elite RGB"
    firmware_version = "2.3.1"
    lcd_resolution = (640, 640)
    driver_class = "PatchedKrakenZ3"

    def connect(self):
        pass

    def read_status(self):
        return DeviceStatus(liquid_temp=41.5, pump_rpm=1850, fan_rpm=900)


def test_doctor_reports_an_active_patch_and_the_device(project, monkeypatch, capsys):
    monkeypatch.setattr(upstream, "installed", lambda: _COMPATIBLE)
    monkeypatch.setattr(cli, "KrakenDevice", _DoctorDevice)
    assert cli.main(["--config", str(project), "doctor"]) == 0
    out = capsys.readouterr().out
    assert "driver patch:  active" in out
    assert "verified release" in out
    assert "NZXT Kraken 2024 Elite RGB (firmware 2.3.1, LCD 640x640)" in out
    assert "PatchedKrakenZ3" in out
    assert "liquid 41.5 °C, pump 1850 rpm, fan 900 rpm" in out


def test_doctor_exit_3_lists_the_reasons_when_the_patch_is_inactive(
        project, monkeypatch, capsys):
    monkeypatch.setattr(upstream, "installed", lambda: _INCOMPATIBLE)
    assert cli.main(["--config", str(project), "doctor", "--no-device"]) == 3
    out = capsys.readouterr().out
    assert "driver patch:  INACTIVE" in out
    assert "- KrakenZ3._send_data changed upstream" in out
    assert "device:" not in out  # --no-device never touches USB


def test_doctor_with_patch_disabled_in_config_is_fine(project, monkeypatch, capsys):
    project.write_text(project.read_text() + "[device]\ndriver_patch = false\n")
    monkeypatch.setattr(upstream, "installed", lambda: _INCOMPATIBLE)
    assert cli.main(["--config", str(project), "doctor", "--no-device"]) == 0
    assert "disabled in the configuration" in capsys.readouterr().out


def test_doctor_reports_an_unreachable_device(project, monkeypatch, capsys):
    monkeypatch.setattr(upstream, "installed", lambda: _COMPATIBLE)
    monkeypatch.setattr(cli, "KrakenDevice", _UnreachableDevice)
    assert cli.main(["--config", str(project), "doctor"]) == 1
    assert "device:        no device" in capsys.readouterr().out


def test_doctor_reports_a_bootloader_with_exit_78(project, monkeypatch, capsys):
    class Wedged(_UnreachableDevice):
        def connect(self):
            raise DeviceInBootloader("stuck in bootloader mode")

    monkeypatch.setattr(upstream, "installed", lambda: _COMPATIBLE)
    monkeypatch.setattr(cli, "KrakenDevice", Wedged)
    assert cli.main(["--config", str(project), "doctor"]) == 78
    assert "stuck in bootloader" in capsys.readouterr().out
