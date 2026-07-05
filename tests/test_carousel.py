import shutil
from pathlib import Path

import pytest

from kraken_lcd.cache import RenderCache
from kraken_lcd.carousel import Carousel
from kraken_lcd.config import (CacheConfig, CarouselConfig, Config,
                               DeviceConfig, RenderConfig)
from kraken_lcd.device import (DeviceError, DeviceInBootloader, DeviceNotFound,
                               DeviceStatus)
from kraken_lcd.sensors import SensorSnapshot


class FakeDevice:
    def __init__(self):
        self.uploads = []
        self.upload_ok = True
        self.cleanups = 0
        self.cooling_applied = None
        self.events = []
        self.connect_failures = 0
        self.on_upload = None

    def connect(self):
        if self.connect_failures > 0:
            self.connect_failures -= 1
            self.events.append("connect-failed")
            raise DeviceNotFound("usb not ready yet")
        self.events.append("connect")

    def disconnect(self):
        self.events.append("disconnect")

    def set_brightness(self, percent):
        self.events.append("brightness")

    def reset_to_liquid(self):
        self.events.append("reset")

    def clear_image_memory(self):
        self.cleanups += 1
        self.events.append("clear")

    def apply_cooling(self, pump, fan):
        self.cooling_applied = (pump, fan)
        self.events.append("cooling")

    def read_status(self):
        return DeviceStatus(liquid_temp=34.2, pump_rpm=1850, fan_rpm=900)

    def show_gif(self, path: Path) -> bool:
        if self.upload_ok:
            self.uploads.append(path)
            if self.on_upload:
                self.on_upload()
        return self.upload_ok


class FakeNotifier:
    def __init__(self):
        self.ready_count = 0
        self.watchdog_count = 0
        self.stopping_count = 0

    def ready(self):
        self.ready_count += 1

    def watchdog(self):
        self.watchdog_count += 1

    def stopping(self):
        self.stopping_count += 1


class FakeSensors:
    def snapshot(self, liquid_temp=None, pump_rpm=None, fan_rpm=None):
        return SensorSnapshot(cpu_load=42.0, cpu_temp=60.5,
                              gpu_load=None, gpu_temp=None,
                              liquid_temp=liquid_temp,
                              pump_rpm=pump_rpm, fan_rpm=fan_rpm)


@pytest.fixture
def cfg(tmp_path, tiny_gif):
    for name in ("liquid.gif", "cpu.gif", "temp.gif"):
        shutil.copy(tiny_gif, tmp_path / name)
    return Config(
        base_dir=tmp_path,
        # short display time is fine here: the safety floor is enforced on
        # config *loading*; direct construction is the test's business
        carousel=CarouselConfig(display_seconds=0.01,
                                screens=("liquid", "cpu", "gpu", "temps")),
        render=RenderConfig(size=96, max_frames=2, colors=16, assets_dir=Path(".")),
        cache=CacheConfig(dir=tmp_path / "cache"),
        device=DeviceConfig(),
    )


def _carousel(cfg, device, notifier=None):
    cache = RenderCache(cfg.cache.dir, int(cfg.cache.max_megabytes * 2**20))
    return Carousel(cfg, device, FakeSensors(), cache,
                    notifier=notifier or FakeNotifier())


def test_one_cycle_uploads_available_tiles(cfg):
    device = FakeDevice()
    carousel = _carousel(cfg, device)
    carousel._one_cycle()
    # gpu tile skipped (no GPU in FakeSensors) -> liquid, cpu, temps remain
    assert len(device.uploads) == 3
    for path in device.uploads:
        assert path.is_file() and path.stat().st_size > 0


def test_second_cycle_hits_the_cache(cfg):
    device = FakeDevice()
    carousel = _carousel(cfg, device)
    carousel._one_cycle()
    carousel._one_cycle()
    # identical sensor values -> same three files uploaded again
    assert len(device.uploads) == 6
    assert device.uploads[:3] == device.uploads[3:]


def test_stop_request_aborts_cycle(cfg):
    device = FakeDevice()
    carousel = _carousel(cfg, device)
    carousel.request_stop()
    carousel._one_cycle()
    assert device.uploads == []


def test_consecutive_upload_failures_raise(cfg):
    device = FakeDevice()
    device.upload_ok = False
    carousel = _carousel(cfg, device)
    with pytest.raises(DeviceError):
        for _ in range(3):
            carousel._one_cycle()


def test_run_full_lifecycle_and_teardown(cfg):
    device = FakeDevice()
    notifier = FakeNotifier()
    carousel = _carousel(cfg, device, notifier)
    device.on_upload = carousel.request_stop  # stop after the first tile
    carousel.run()
    # setup order, then teardown even though we stopped mid-cycle
    assert device.events[:4] == ["connect", "clear", "cooling", "brightness"]
    assert device.events[-2:] == ["reset", "disconnect"]
    assert device.cooling_applied == (None, None)
    assert notifier.ready_count == 1
    assert notifier.watchdog_count >= 1
    assert notifier.stopping_count == 1


def test_connect_retry_recovers_from_boot_race(cfg, monkeypatch):
    monkeypatch.setattr("kraken_lcd.carousel.CONNECT_RETRY_SECONDS", 0.01)
    device = FakeDevice()
    device.connect_failures = 2  # third attempt succeeds
    carousel = _carousel(cfg, device)
    device.on_upload = carousel.request_stop
    carousel.run()
    assert device.events.count("connect-failed") == 2
    assert "connect" in device.events
    assert device.events[-2:] == ["reset", "disconnect"]


def test_connect_gives_up_after_all_attempts(cfg, monkeypatch):
    monkeypatch.setattr("kraken_lcd.carousel.CONNECT_RETRY_SECONDS", 0.01)
    device = FakeDevice()
    device.connect_failures = 99
    carousel = _carousel(cfg, device)
    with pytest.raises(DeviceError):
        carousel.run()
    assert device.events.count("connect-failed") == 3  # CONNECT_ATTEMPTS


def test_bootloader_is_never_retried(cfg):
    device = FakeDevice()
    calls = []

    def bootloader_connect():
        calls.append(1)
        raise DeviceInBootloader("wedged")

    device.connect = bootloader_connect
    carousel = _carousel(cfg, device)
    with pytest.raises(DeviceInBootloader):
        carousel.run()
    assert len(calls) == 1  # no pointless retries against a wedged device


def test_signal_handlers_installed(cfg):
    import signal
    carousel = _carousel(cfg, FakeDevice())
    old_term = signal.getsignal(signal.SIGTERM)
    old_int = signal.getsignal(signal.SIGINT)
    try:
        carousel.install_signal_handlers()
        assert signal.getsignal(signal.SIGTERM) == carousel.request_stop
        assert signal.getsignal(signal.SIGINT) == carousel.request_stop
    finally:
        signal.signal(signal.SIGTERM, old_term)
        signal.signal(signal.SIGINT, old_int)


def test_prerender_avoids_duplicate_renders(cfg, monkeypatch):
    from kraken_lcd import carousel as carousel_mod
    real_render = carousel_mod.render_gif
    renders = []

    def counting_render(background, elements, out, render_cfg, **kwargs):
        renders.append(out)
        return real_render(background, elements, out, render_cfg, **kwargs)

    monkeypatch.setattr(carousel_mod, "render_gif", counting_render)
    device = FakeDevice()
    carousel = _carousel(cfg, device)
    carousel._one_cycle()
    # 3 shown tiles (gpu is skipped): each rendered exactly once, whether by
    # the prerender step or the show step — never twice
    assert len(renders) == 3
    carousel._one_cycle()
    assert len(renders) == 3  # second cycle: everything is a cache hit


def test_pump_tile_uses_device_status(cfg, tiny_gif):
    import shutil
    shutil.copy(tiny_gif, cfg.base_dir / "liquid.gif")  # pump default background
    cfg = Config(base_dir=cfg.base_dir,
                 carousel=CarouselConfig(display_seconds=0.01, screens=("pump",)),
                 render=cfg.render, cache=cfg.cache, device=cfg.device)
    device = FakeDevice()
    carousel = _carousel(cfg, device)
    carousel._one_cycle()
    assert len(device.uploads) == 1  # 1850 rpm from FakeDevice.read_status
