from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

from kraken_lcd.config import FACE_SCREENS, RenderConfig
from kraken_lcd.faces import FACES, FaceContext, decode, encode, render_face
from kraken_lcd.screens import build_screens
from kraken_lcd.sensors import SensorSnapshot

ASSETS = Path(__file__).resolve().parent.parent / "assets"
SIZE = 160  # small, so the whole set renders quickly
HISTORY = {"liquid": tuple(40 + (i % 9) for i in range(60)),
           "cpu_load": tuple(float(i % 100) for i in range(60)),
           "gpu_load": tuple(5.0 for _ in range(60)),
           "cpu_temp": tuple(70.0 for _ in range(60))}
SNAP = SensorSnapshot(cpu_load=93.0, cpu_temp=91.0, gpu_load=6.0, gpu_temp=52.0,
                      ram_percent=60.0, nvme_temp=64.0, liquid_temp=47.6, pump_rpm=2300,
                      fan_rpm=1100, cpu_core_loads=(90.0, 80.0, 100.0, 60.0),
                      cpu_freq_ghz=5.07, ram_used_gb=37.2, ram_total_gb=61.4,
                      gpu_mem_used_gb=4.6, gpu_power_w=37.0, history=HISTORY)
NIGHT = datetime(2026, 9, 25, 3, 30)


def _context(**kwargs) -> FaceContext:
    return FaceContext(size=SIZE, assets_dir=ASSETS, language="de", now=NIGHT, **kwargs)


def test_every_face_screen_is_registered():
    assert set(FACES) == set(FACE_SCREENS)


def test_values_survive_the_round_trip_through_elements():
    values = FACES["cockpit"].values(SNAP, _context())
    assert decode(encode(values)) == values


@pytest.mark.parametrize("name", FACE_SCREENS)
def test_face_renders_a_looping_gif_in_budget(name, tmp_path):
    face = FACES[name]
    context = _context()
    values = face.values(SNAP, context)
    assert values is not None, f"{name} should apply to the sample snapshot"
    out = tmp_path / f"{name}.gif"
    render_face(face, encode(values), out, RenderConfig(size=SIZE, colors=64), context,
                budget_bytes=4 * 2**20)
    with Image.open(out) as gif:
        assert gif.size == (SIZE, SIZE)
        assert gif.info.get("loop") == 0
        # identical neighbouring frames are merged, but every face moves
        assert getattr(gif, "n_frames", 1) >= 2


def test_alarm_only_above_the_limit():
    face = FACES["alarm"]
    assert face.values(SensorSnapshot(cpu_temp=70.0, gpu_temp=60.0), _context()) is None
    hot = face.values(SensorSnapshot(cpu_temp=70.0, gpu_temp=95.0), _context())
    assert hot["name"] == "GPU" and hot["temp"] == 95
    lowered = face.values(SensorSnapshot(cpu_temp=70.0), _context(threshold=65.0))
    assert lowered["name"] == "CPU" and lowered["limit"] == 65


def test_night_only_inside_its_hours():
    face = FACES["night"]

    def at(hour, **kwargs):
        return FaceContext(size=SIZE, assets_dir=ASSETS, now=datetime(2026, 9, 25, hour), **kwargs)

    assert face.values(SNAP, at(23)) is not None
    assert face.values(SNAP, at(3)) is not None
    assert face.values(SNAP, at(12)) is None
    assert face.values(SNAP, at(12, hours=(9, 17))) is not None


def test_history_needs_enough_points():
    face = FACES["history"]
    assert face.values(SensorSnapshot(liquid_temp=40.0), _context()) is None
    short = SensorSnapshot(history={"liquid": (40.0,) * 5})
    assert face.values(short, _context()) is None
    assert face.values(SNAP, _context())["max"] == 48


def test_face_screens_carry_their_values_as_data_elements():
    screens = build_screens({}, language="de")
    elements = screens["fire"].build(SNAP, None)
    assert {e.role for e in elements} == {"data"}
    assert decode(elements)["load"] == 93
    assert screens["fire"].background == "cpu.gif"
    assert screens["cockpit"].background == ""
