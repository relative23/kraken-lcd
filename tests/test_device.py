import logging

import pytest

from kraken_lcd.config import DeviceConfig
from kraken_lcd.device import (
    DeviceInBootloader,
    DeviceNotFound,
    DeviceStatus,
    DeviceUnsupported,
    KrakenDevice,
)

_liquidctl_log = logging.getLogger("liquidctl.driver.kraken3")


class FakeDriver:
    description = "Fake NZXT Kraken"

    def __init__(self):
        self.calls = []
        self.fixed_speeds = []
        self.profiles = []
        self.fail_next = 0     # raise an exception on the next N uploads
        self.reject_next = 0   # firmware-style rejection: log ERROR, no raise
        self.garbage_reads = 0  # deliver implausible status for the next N reads
        self.cleanups = 0
        self.connected = False
        self.liquid_temp = 33.7

    def connect(self):
        self.connected = True

    def disconnect(self):
        self.connected = False

    def initialize(self):
        return [("Firmware version", "2.3.1", ""), ("LCD", "on", "")]

    def get_status(self):
        liquid = self.liquid_temp
        if self.garbage_reads > 0:
            self.garbage_reads -= 1
            liquid = 2.0
        return [("Liquid temperature", liquid, "°C"),
                ("Pump speed", 1866, "rpm"),
                ("Fan speed", 914, "rpm")]

    def set_screen(self, channel, mode, value):
        if self.fail_next > 0:
            self.fail_next -= 1
            raise OSError("simulated usb error")
        if self.reject_next > 0:
            self.reject_next -= 1
            _liquidctl_log.error("Failed to setup bucket for data transfer")
            return
        self.calls.append((channel, mode, value))

    def set_fixed_speed(self, channel, duty):
        self.fixed_speeds.append((channel, duty))

    def set_speed_profile(self, channel, profile):
        self.profiles.append((channel, list(profile)))

    def _delete_all_buckets(self):
        self.cleanups += 1


class FakePatchedDriver(FakeDriver):
    """Like the patched driver: offers a flash-free soft clear."""

    def __init__(self):
        super().__init__()
        self.soft_clears = 0

    def soft_clear_inactive(self):
        self.soft_clears += 1


class RecordingMonitor:
    """Captures every record()/flush() so tests can assert the wiring."""

    def __init__(self):
        self.records = []
        self.flushes = 0

    def record(self, *, succeeded, refusals):
        self.records.append((succeeded, refusals))

    def flush(self):
        self.flushes += 1


def _device(monkeypatch, cfg: DeviceConfig | None = None, driver=None,
            monitor=None):
    driver = driver or FakeDriver()
    dev = KrakenDevice(cfg or DeviceConfig(), finder=lambda: driver,
                       monitor=monitor)
    monkeypatch.setattr("kraken_lcd.device.time.sleep", lambda s: None)
    # tests must not depend on what is on this machine's USB bus
    monkeypatch.setattr("kraken_lcd.device._bootloader_present", lambda: False)
    dev.connect()
    return dev, driver


@pytest.fixture
def device(monkeypatch):
    return _device(monkeypatch)


def _gif(tmp_path, megabytes=0.001, name="tile.gif"):
    path = tmp_path / name
    path.write_bytes(b"x" * int(megabytes * 1024 * 1024))
    return path


def test_upload_success(device, tmp_path):
    dev, driver = device
    assert dev.show_gif(_gif(tmp_path)) is True
    assert driver.calls[-1][:2] == ("lcd", "gif")


def test_size_guard_blocks_oversized_upload(device, tmp_path):
    dev, driver = device
    assert dev.show_gif(_gif(tmp_path, megabytes=5)) is False
    assert driver.calls == []  # the device was never touched


def test_retry_recovers_from_transient_error(device, tmp_path):
    dev, driver = device
    driver.fail_next = 1
    assert dev.show_gif(_gif(tmp_path)) is True
    assert driver.connected  # reconnect happened


def test_gives_up_after_all_retries(device, tmp_path):
    dev, driver = device
    driver.fail_next = 99
    assert dev.show_gif(_gif(tmp_path)) is False


def test_firmware_rejection_triggers_cleanup_then_succeeds(device, tmp_path):
    dev, driver = device
    driver.reject_next = 1
    assert dev.show_gif(_gif(tmp_path)) is True
    assert driver.cleanups == 1  # image memory was cleared between attempts


def test_persistent_firmware_rejection_returns_false(device, tmp_path):
    dev, driver = device
    driver.reject_next = 99
    assert dev.show_gif(_gif(tmp_path)) is False


def test_proactive_cleanup_before_memory_overflows(monkeypatch, tmp_path):
    # budget of ~1.5 KB, uploads of ~1 KB: the second upload must trigger a
    # proactive cleanup instead of running into a firmware rejection
    cfg = DeviceConfig(image_memory_megabytes=0.0015)
    dev, driver = _device(monkeypatch, cfg)
    assert dev.show_gif(_gif(tmp_path, name="a.gif")) is True
    assert driver.cleanups == 0
    assert dev.show_gif(_gif(tmp_path, name="b.gif")) is True
    assert driver.cleanups == 1
    assert len(driver.calls) == 2  # both uploads reached the device exactly once


def test_proactive_cleanup_prefers_flash_free_soft_clear(monkeypatch, tmp_path):
    cfg = DeviceConfig(image_memory_megabytes=0.0015)
    dev, driver = _device(monkeypatch, cfg, driver=FakePatchedDriver())
    assert dev.show_gif(_gif(tmp_path, name="a.gif")) is True
    assert dev.show_gif(_gif(tmp_path, name="b.gif")) is True
    assert driver.soft_clears == 1
    assert driver.cleanups == 0  # never the visible full clear


def test_soft_clear_keeps_active_bucket_on_the_books(monkeypatch, tmp_path):
    # after a soft clear the displayed bucket still occupies memory, so the
    # accounting must not reset to zero
    dev, driver = _device(monkeypatch, driver=FakePatchedDriver())
    assert dev.show_gif(_gif(tmp_path, megabytes=0.002)) is True
    dev.clear_image_memory()
    assert dev._bytes_since_cleanup == int(0.002 * 1024 * 1024)


def test_firmware_rejection_uses_full_clear(monkeypatch, tmp_path):
    # a real refusal means the memory state is suspect: full clear, not soft
    dev, driver = _device(monkeypatch, driver=FakePatchedDriver())
    driver.reject_next = 1
    assert dev.show_gif(_gif(tmp_path)) is True
    assert driver.cleanups == 1
    assert driver.soft_clears == 0


def test_bucket_setup_refused_is_treated_as_rejection(monkeypatch, tmp_path):
    from kraken_lcd.driver_patch import BucketSetupRefused
    dev, driver = _device(monkeypatch, driver=FakePatchedDriver())
    original = driver.set_screen
    state = {"raised": False}

    def refusing_set_screen(channel, mode, value):
        if not state["raised"]:
            state["raised"] = True
            raise BucketSetupRefused("device refused the bucket setup twice")
        original(channel, mode, value)

    driver.set_screen = refusing_set_screen
    assert dev.show_gif(_gif(tmp_path)) is True  # cleared fully + retried
    assert driver.cleanups == 1


def test_read_status_parses_all_values(device):
    dev, _ = device
    assert dev.read_status() == DeviceStatus(liquid_temp=33.7,
                                             pump_rpm=1866, fan_rpm=914)


def test_read_status_retries_past_garbage_readings(device):
    dev, driver = device
    driver.garbage_reads = 2  # connect() already consumed one flush read
    status = dev.read_status(attempts=3)
    assert status.liquid_temp == 33.7


def test_implausible_liquid_temperature_filtered(device):
    dev, driver = device
    driver.garbage_reads = 99
    assert dev.read_status(attempts=2).liquid_temp is None
    driver.garbage_reads = 0
    driver.liquid_temp = 130.0
    assert dev.read_status(attempts=1).liquid_temp is None


def _count_connects(driver):
    connects = []
    original = driver.connect
    driver.connect = lambda: connects.append(1) or original()
    return connects


def test_status_failures_force_reconnect_before_upload(device, tmp_path):
    # transport down for one full read_status(), then recovered: the next
    # upload must reconnect first instead of streaming right away
    dev, driver = device
    original = driver.get_status

    def broken_status():
        raise OSError("usb transport down")

    driver.get_status = broken_status
    assert dev.read_status().liquid_temp is None  # 3 consecutive errors
    driver.get_status = original
    connects = _count_connects(driver)
    assert dev.show_gif(_gif(tmp_path)) is True
    assert connects == [1]


def test_status_failures_reconnect_detects_bootloader(monkeypatch, tmp_path):
    # the reconnect forced by the breaker must notice a device that fell
    # into its bootloader — and nothing may be streamed before that
    dev, driver = _device(monkeypatch)

    def broken_status():
        raise OSError("usb transport down")

    driver.get_status = broken_status
    dev.read_status()
    monkeypatch.setattr("kraken_lcd.device._bootloader_present", lambda: True)
    with pytest.raises(DeviceInBootloader):
        dev.show_gif(_gif(tmp_path))
    assert driver.calls == []  # no upload reached the sick device


def test_recovered_status_read_resets_the_breaker(device, tmp_path):
    # 2 errors, then a good read: no reconnect on the next upload
    dev, driver = device
    original = driver.get_status
    state = {"fail": 2}

    def flaky_status():
        if state["fail"]:
            state["fail"] -= 1
            raise OSError("hiccup")
        return original()

    driver.get_status = flaky_status
    assert dev.read_status().liquid_temp == 33.7
    connects = _count_connects(driver)
    assert dev.show_gif(_gif(tmp_path)) is True
    assert connects == []


def test_monitor_records_successful_upload(monkeypatch, tmp_path):
    mon = RecordingMonitor()
    dev, _ = _device(monkeypatch, monitor=mon)
    assert dev.show_gif(_gif(tmp_path)) is True
    assert mon.records == [(True, 0)]


def test_monitor_records_failed_upload(monkeypatch, tmp_path):
    mon = RecordingMonitor()
    dev, driver = _device(monkeypatch, monitor=mon)
    driver.fail_next = 99
    assert dev.show_gif(_gif(tmp_path)) is False
    assert mon.records == [(False, 0)]


def test_monitor_counts_a_firmware_bucket_refusal(monkeypatch, tmp_path):
    # the patched driver exposes last_upload_refused; a recovered refusal
    # must show up in the monitor even though the upload itself succeeded
    mon = RecordingMonitor()
    dev, driver = _device(monkeypatch, driver=FakePatchedDriver(), monitor=mon)
    original = driver.set_screen

    def refusing_then_ok(channel, mode, value):
        driver.last_upload_refused = True  # firmware balked, driver recovered
        original(channel, mode, value)

    driver.set_screen = refusing_then_ok
    assert dev.show_gif(_gif(tmp_path)) is True
    assert mon.records == [(True, 1)]


def test_monitor_not_recorded_for_oversized_upload(monkeypatch, tmp_path):
    # the size guard never touches the device, so it is not an upload event
    mon = RecordingMonitor()
    dev, _ = _device(monkeypatch, monitor=mon)
    assert dev.show_gif(_gif(tmp_path, megabytes=5)) is False
    assert mon.records == []


def test_flush_upload_stats_delegates_to_the_monitor(monkeypatch):
    mon = RecordingMonitor()
    dev, _ = _device(monkeypatch, monitor=mon)
    dev.flush_upload_stats()
    assert mon.flushes == 1


def test_apply_cooling_fixed_and_curve(device):
    dev, driver = device
    dev.apply_cooling(pump=60, fan=((30.0, 30), (40.0, 100)))
    assert driver.fixed_speeds == [("pump", 60)]
    assert driver.profiles == [("fan", [(30.0, 30), (40.0, 100)])]


def test_apply_cooling_none_leaves_firmware_defaults(device):
    dev, driver = device
    dev.apply_cooling(pump=None, fan=None)
    assert driver.fixed_speeds == [] and driver.profiles == []


def test_apply_cooling_survives_driver_errors(device):
    dev, driver = device

    def boom(channel, duty):
        raise OSError("firmware said no")

    driver.set_fixed_speed = boom
    dev.apply_cooling(pump=60, fan=None)  # must not raise


def test_brightness_and_reset(device):
    dev, driver = device
    dev.set_brightness(80)
    dev.reset_to_liquid()
    assert ("lcd", "brightness", "80") in driver.calls
    assert ("lcd", "liquid", None) in driver.calls


def test_device_not_found(monkeypatch):
    monkeypatch.setattr("kraken_lcd.device._bootloader_present", lambda: False)
    dev = KrakenDevice(DeviceConfig(), finder=lambda: None)
    with pytest.raises(DeviceNotFound):
        dev.connect()


def test_crashing_discovery_with_bootloader_on_bus(monkeypatch):
    # liquidctl enumeration crashes with ValueError while a 1e71:3011
    # bootloader device is present (observed live 2026-07-05)
    calls = iter([False, True])  # absent pre-check, present after the crash
    monkeypatch.setattr("kraken_lcd.device._bootloader_present",
                        lambda: next(calls))

    def crashing_finder():
        raise ValueError("The device has no langid")

    dev = KrakenDevice(DeviceConfig(), finder=crashing_finder)
    with pytest.raises(DeviceInBootloader):
        dev.connect()


def test_crashing_discovery_without_bootloader(monkeypatch):
    monkeypatch.setattr("kraken_lcd.device._bootloader_present", lambda: False)

    def crashing_finder():
        raise ValueError("hidapi hiccup")

    dev = KrakenDevice(DeviceConfig(), finder=crashing_finder)
    with pytest.raises(DeviceNotFound):
        dev.connect()


def test_bootloader_detected(monkeypatch):
    monkeypatch.setattr("kraken_lcd.device._bootloader_present", lambda: True)
    dev = KrakenDevice(DeviceConfig(), finder=lambda: None)
    with pytest.raises(DeviceInBootloader):
        dev.connect()


def test_firmware_version_and_driver_class_are_captured(device):
    dev, _ = device
    assert dev.firmware_version == "2.3.1"
    assert dev.driver_class == "FakeDriver"
    dev.disconnect()
    assert dev.driver_class == "none"


def test_firmware_version_survives_a_failing_initialize(monkeypatch):
    driver = FakeDriver()

    def broken_initialize():
        raise OSError("init timeout")

    driver.initialize = broken_initialize
    dev, _ = _device(monkeypatch, driver=driver)
    assert dev.firmware_version is None  # connected anyway (existing behaviour)


def test_unsupported_firmware_is_not_retried(monkeypatch, tmp_path):
    # Kraken 2023 on firmware 2.x: liquidctl raises NotSupportedByDriver for
    # GIFs. That must surface as DeviceUnsupported immediately — no retry,
    # no reconnect, no "3 consecutive failures" restart loop.
    from liquidctl.error import NotSupportedByDriver
    dev, driver = _device(monkeypatch)
    attempts = []

    def refusing(channel, mode, value):
        attempts.append(mode)
        raise NotSupportedByDriver("gif images are not supported on firmware 2.X.Y")

    driver.set_screen = refusing
    connects = _count_connects(driver)
    with pytest.raises(DeviceUnsupported) as info:
        dev.show_gif(_gif(tmp_path))
    assert attempts == ["gif"]
    assert connects == []
    assert "firmware 2.3.1" in str(info.value)  # from the device, for the report
    assert "cannot show GIFs" in str(info.value)


def test_not_found_message_lists_supported_devices(monkeypatch):
    monkeypatch.setattr("kraken_lcd.device._bootloader_present", lambda: False)
    dev = KrakenDevice(DeviceConfig(), finder=lambda: None)
    with pytest.raises(DeviceNotFound) as info:
        dev.connect()
    assert "1e71:3012" in str(info.value)
    assert "1e71:3008" in str(info.value)


def test_patch_fallback_is_logged_once(monkeypatch, caplog):
    from kraken_lcd import device as device_mod

    def incompatible():
        raise RuntimeError("KrakenZ3._send_data changed upstream")

    monkeypatch.setattr("kraken_lcd.driver_patch.find_patched_kraken", incompatible)
    stock = FakeDriver()
    monkeypatch.setattr(device_mod, "_find_stock_kraken", lambda: stock)
    monkeypatch.setattr(device_mod, "_patch_fallback_logged", False)
    with caplog.at_level(logging.DEBUG, logger="kraken_lcd.device"):
        assert device_mod._find_kraken(use_patch=True) is stock
        assert device_mod._find_kraken(use_patch=True) is stock
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "_send_data changed upstream" in warnings[0].getMessage()


def test_patch_disabled_by_config_skips_the_patch(monkeypatch):
    from kraken_lcd import device as device_mod
    stock = FakeDriver()
    monkeypatch.setattr(device_mod, "_find_stock_kraken", lambda: stock)

    def must_not_be_called():
        raise AssertionError("patch must not be consulted")

    monkeypatch.setattr("kraken_lcd.driver_patch.find_patched_kraken", must_not_be_called)
    assert device_mod._find_kraken(use_patch=False) is stock


def test_displayed_upload_tracks_the_lcd_content(device, tmp_path):
    dev, driver = device
    assert dev.displayed_upload is None
    path = _gif(tmp_path)
    assert dev.show_gif(path) is True
    assert dev.displayed_upload == path
    driver.fail_next = 99
    assert dev.show_gif(_gif(tmp_path, name="other.gif")) is False
    assert dev.displayed_upload is None  # a failed upload leaves it unknown


def test_displayed_upload_survives_a_soft_clear_but_not_a_full_one(monkeypatch, tmp_path):
    dev, driver = _device(monkeypatch, driver=FakePatchedDriver())
    path = _gif(tmp_path)
    dev.show_gif(path)
    dev.clear_image_memory()            # soft: the displayed bucket stays
    assert dev.displayed_upload == path
    dev.clear_image_memory(full=True)   # full: LCD switches to liquid view
    assert dev.displayed_upload is None


def test_displayed_upload_is_forgotten_on_reset_and_disconnect(device, tmp_path):
    dev, _ = device
    dev.show_gif(_gif(tmp_path))
    dev.reset_to_liquid()
    assert dev.displayed_upload is None
    dev.show_gif(_gif(tmp_path))
    dev.disconnect()
    assert dev.displayed_upload is None
