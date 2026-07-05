import subprocess

import psutil

from kraken_lcd import sensors as sensors_mod
from kraken_lcd.sensors import SensorReader, SensorSnapshot


class _Temp:
    def __init__(self, current: float, label: str = "") -> None:
        self.current = current
        self.label = label


class _Memory:
    percent = 26.5


def _boom(*args, **kwargs):
    raise RuntimeError("sensor exploded")


def test_snapshot_happy_path(monkeypatch):
    monkeypatch.setattr(psutil, "cpu_percent", lambda interval=None: 37.5)
    monkeypatch.setattr(psutil, "virtual_memory", lambda: _Memory())
    monkeypatch.setattr(psutil, "sensors_temperatures", lambda: {
        "k10temp": [_Temp(61.2, "Tctl")],
        "nvme": [_Temp(59.9, "Composite"), _Temp(71.9, "Sensor 2"),
                 _Temp(57.9, "Composite")],
    })

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="55, 23\n", stderr="")

    monkeypatch.setattr(sensors_mod.subprocess, "run", fake_run)

    snap = SensorReader().snapshot(liquid_temp=33.4, pump_rpm=1866, fan_rpm=914)
    assert snap == SensorSnapshot(cpu_load=37.5, cpu_temp=61.2,
                                  gpu_load=23.0, gpu_temp=55.0,
                                  ram_percent=26.5, nvme_temp=59.9,
                                  liquid_temp=33.4, pump_rpm=1866, fan_rpm=914)


def test_nvme_uses_hottest_composite_not_raw_sensor(monkeypatch):
    monkeypatch.setattr(psutil, "sensors_temperatures", lambda: {
        "nvme": [_Temp(50.0, "Composite"), _Temp(80.0, "Sensor 2"),
                 _Temp(60.0, "Composite")],
    })
    reader = SensorReader()
    # Sensor 2 (80°) is a hotspot value, not the drive temperature
    assert reader._read_nvme_temp(reader._read_temperatures()) == 60.0


def test_nvme_falls_back_to_unlabelled_entries(monkeypatch):
    monkeypatch.setattr(psutil, "sensors_temperatures",
                        lambda: {"nvme": [_Temp(48.0)]})
    reader = SensorReader()
    assert reader._read_nvme_temp(reader._read_temperatures()) == 48.0


def test_coretemp_fallback(monkeypatch):
    monkeypatch.setattr(psutil, "sensors_temperatures",
                        lambda: {"coretemp": [_Temp(48.0)]})
    reader = SensorReader()
    assert reader._read_cpu_temp(reader._read_temperatures()) == 48.0


def test_all_sensors_failing_degrade_to_none(monkeypatch):
    monkeypatch.setattr(psutil, "cpu_percent", _boom)
    monkeypatch.setattr(psutil, "virtual_memory", _boom)
    monkeypatch.setattr(psutil, "sensors_temperatures", _boom)
    monkeypatch.setattr(sensors_mod.subprocess, "run", _boom)

    snap = SensorReader().snapshot()
    assert snap == SensorSnapshot()  # everything None, no exception


def test_gpu_failure_warns_only_once(monkeypatch, caplog):
    monkeypatch.setattr(sensors_mod.subprocess, "run", _boom)
    monkeypatch.setattr(sensors_mod.glob, "glob", lambda pattern: [])
    reader = SensorReader()
    with caplog.at_level("WARNING", logger="kraken_lcd.sensors"):
        reader._read_gpu({})
        reader._read_gpu({})
    warnings = [r for r in caplog.records if "no GPU stats" in r.getMessage()]
    assert len(warnings) == 1
