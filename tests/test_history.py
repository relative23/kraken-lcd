import json

from kraken_lcd.history import History
from kraken_lcd.sensors import SensorSnapshot


def test_records_at_most_once_a_minute(tmp_path):
    history = History(tmp_path / "history.json")
    history.record(SensorSnapshot(liquid_temp=40.0), now=1000.0)
    history.record(SensorSnapshot(liquid_temp=41.0), now=1030.0)  # too soon
    history.record(SensorSnapshot(liquid_temp=42.0), now=1061.0)
    assert history.series()["liquid"] == (40.0, 42.0)


def test_keeps_one_day_and_skips_gaps(tmp_path):
    history = History(None)
    history.record(SensorSnapshot(liquid_temp=30.0), now=0.0)
    history.record(SensorSnapshot(cpu_load=50.0), now=100.0)
    history.record(SensorSnapshot(liquid_temp=31.0), now=86_500.0)  # the first point expires
    series = history.series()
    assert series["liquid"] == (31.0,)
    assert series["cpu_load"] == (50.0,)


def test_survives_a_restart(tmp_path):
    path = tmp_path / "history.json"
    first = History(path)
    first.record(SensorSnapshot(liquid_temp=40.0, cpu_temp=70.0), now=1000.0)
    first.save()
    assert History(path).series()["cpu_temp"] == (70.0,)


def test_a_broken_file_starts_empty(tmp_path):
    path = tmp_path / "history.json"
    path.write_text("{not json")
    assert History(path).series()["liquid"] == ()
    path.write_text(json.dumps({"points": [[1, 2]]}))  # wrong shape is dropped
    assert History(path).series()["liquid"] == ()
