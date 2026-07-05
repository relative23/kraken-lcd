"""Kraken device access via the liquidctl library.

One persistent connection for the whole runtime; every upload is paced,
size-guarded and accounted against the device's image memory. Background:
in 2026-06 oversized GIFs plus rapid mode switching wedged this Kraken into
its bootloader (USB 1e71:3011), recoverable only by cutting standby power.
This module is written so that cannot happen again.
"""

import logging
import time
from dataclasses import dataclass
from pathlib import Path

from .config import DeviceConfig, SpeedSetting

log = logging.getLogger(__name__)

NZXT_VENDOR_ID = 0x1E71
BOOTLOADER_PRODUCT_ID = 0x3011

BOOTLOADER_RECOVERY = (
    "Kraken is stuck in bootloader mode (USB 1e71:3011). Recovery: shut the "
    "machine down, switch the PSU off (or unplug) for ~30 seconds, then boot. "
    "A plain reboot is NOT enough — the device keeps standby power."
)

# systemd catches this via RestartPreventExitStatus so a wedged device is
# never hammered with restart attempts.
EXIT_BOOTLOADER = 78


class DeviceError(Exception):
    """Base class for device problems."""


class DeviceNotFound(DeviceError):
    pass


class DeviceInBootloader(DeviceError):
    pass


@dataclass(frozen=True)
class DeviceStatus:
    liquid_temp: float | None = None
    pump_rpm: int | None = None
    fan_rpm: int | None = None


def _find_stock_kraken():
    from liquidctl import find_liquidctl_devices
    for candidate in find_liquidctl_devices():
        if "kraken" in candidate.description.lower() and hasattr(candidate, "set_screen"):
            return candidate
    return None


def _find_kraken(use_patch: bool = True):
    if use_patch:
        from .driver_patch import find_patched_kraken
        try:
            return find_patched_kraken()
        except RuntimeError as exc:
            log.warning("driver patch disabled itself (%s) — using the stock "
                        "driver; occasional LCD flashes on bucket cleanup "
                        "are possible", exc)
    return _find_stock_kraken()


def _bootloader_present() -> bool:
    try:
        import usb.core
        return usb.core.find(idVendor=NZXT_VENDOR_ID,
                             idProduct=BOOTLOADER_PRODUCT_ID) is not None
    except Exception:
        return False


class _DriverErrorWatcher(logging.Handler):
    """Collects ERROR records from liquidctl during one operation.

    The KrakenZ3 driver does not raise on firmware-level upload failures
    ("Failed to setup bucket ..."), it only logs them — this is the only
    reliable way to notice that an upload did not reach the screen.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.errors: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.errors.append(record.getMessage())


class KrakenDevice:
    MIN_UPLOAD_INTERVAL = 5.0  # seconds; hard floor, independent of config
    PLAUSIBLE_LIQUID_RANGE = (5.0, 90.0)  # °C; reads after init can be garbage
    PLAUSIBLE_RPM_RANGE = (0, 10_000)
    STATUS_RETRY_DELAY = 0.5  # seconds between retries on implausible reads

    def __init__(self, cfg: DeviceConfig, finder=None) -> None:
        self._cfg = cfg
        self._finder = finder or (lambda: _find_kraken(cfg.driver_patch))
        self._driver = None
        self._last_upload = 0.0
        self._bytes_since_cleanup = 0
        self._last_upload_size = 0

    @property
    def description(self) -> str:
        return self._driver.description if self._driver else "not connected"

    @property
    def lcd_resolution(self) -> tuple[int, int] | None:
        """(width, height) of the connected device's LCD, if known.

        The KrakenZ3 family spans 240x240, 320x320 and 640x640 panels; the
        driver knows which one it is talking to.
        """
        if self._driver is None:
            return None
        resolution = getattr(self._driver, "lcd_resolution", None)
        if (isinstance(resolution, (tuple, list)) and len(resolution) == 2
                and all(isinstance(v, int) and v >= 64 for v in resolution)):
            return (resolution[0], resolution[1])
        return None

    def connect(self) -> None:
        # check for the bootloader FIRST: liquidctl's enumeration itself can
        # crash (ValueError: "no langid") while a 1e71:3011 device is on the
        # bus, observed live on 2026-07-05
        if _bootloader_present():
            raise DeviceInBootloader(BOOTLOADER_RECOVERY)
        try:
            driver = self._finder()
        except Exception as exc:
            if _bootloader_present():
                raise DeviceInBootloader(BOOTLOADER_RECOVERY) from exc
            raise DeviceNotFound(f"device discovery failed: {exc}") from exc
        if driver is None:
            if _bootloader_present():
                raise DeviceInBootloader(BOOTLOADER_RECOVERY)
            raise DeviceNotFound(
                "no NZXT Kraken with an LCD found (expected USB 1e71:3012)")
        driver.connect()
        self._driver = driver
        try:
            driver.initialize()
            driver.get_status()  # first read after init is often bogus; discard
        except Exception as exc:
            log.warning("initialize() failed, continuing anyway: %s", exc)
        log.info("connected to %s", driver.description)

    def disconnect(self) -> None:
        if self._driver is None:
            return
        try:
            self._driver.disconnect()
        except Exception as exc:
            log.debug("disconnect failed: %s", exc)
        self._driver = None

    def _reconnect(self) -> None:
        log.info("reconnecting to the device ...")
        self.disconnect()
        time.sleep(2.0)
        self.connect()

    # ---------------------------------------------------------------- status

    def read_status(self, attempts: int = 3) -> DeviceStatus:
        """Read liquid temperature and pump/fan speeds.

        Retries a few times when the device delivers implausible values,
        which happens for a short moment right after initialization.
        """
        status = DeviceStatus()
        for attempt in range(attempts):
            status = self._read_status_once()
            if status.liquid_temp is not None:
                return status
            if attempt < attempts - 1:
                time.sleep(self.STATUS_RETRY_DELAY)
        return status

    def _read_status_once(self) -> DeviceStatus:
        if self._driver is None:
            return DeviceStatus()
        liquid = pump = fan = None
        try:
            for name, value, _unit in self._driver.get_status():
                label = str(name).lower()
                if "liquid temperature" in label:
                    temp = float(value)
                    low, high = self.PLAUSIBLE_LIQUID_RANGE
                    if low <= temp <= high:
                        liquid = temp
                    else:
                        log.debug("ignoring implausible liquid temperature: %.1f °C", temp)
                elif "pump speed" in label:
                    pump = self._plausible_rpm(value)
                elif "fan speed" in label:
                    fan = self._plausible_rpm(value)
        except Exception as exc:
            log.warning("reading device status failed: %s", exc)
        return DeviceStatus(liquid_temp=liquid, pump_rpm=pump, fan_rpm=fan)

    def _plausible_rpm(self, value) -> int | None:
        try:
            rpm = int(value)
        except (TypeError, ValueError):
            return None
        low, high = self.PLAUSIBLE_RPM_RANGE
        return rpm if low <= rpm <= high else None

    # --------------------------------------------------------------- control

    def set_brightness(self, percent: int) -> None:
        if self._driver is None:
            raise DeviceError("not connected")
        try:
            self._driver.set_screen("lcd", "brightness", str(int(percent)))
        except Exception as exc:
            log.warning("setting brightness failed: %s", exc)

    def reset_to_liquid(self) -> None:
        """Hand the LCD back to the firmware's built-in liquid-temp screen."""
        if self._driver is None:
            return
        try:
            self._driver.set_screen("lcd", "liquid", None)
            log.info("LCD reset to the built-in liquid screen")
        except Exception as exc:
            log.warning("resetting LCD to liquid mode failed: %s", exc)

    def apply_cooling(self, pump: SpeedSetting, fan: SpeedSetting) -> None:
        """Apply fixed duties or (temp, duty) curves for pump and fan.

        Curves are stored on the device and interpolated by its firmware
        against the liquid temperature — they keep working even when this
        daemon is stopped. Failures are logged, never fatal: cooling then
        stays on the previous/firmware setting.
        """
        if self._driver is None:
            raise DeviceError("not connected")
        for channel, setting in (("pump", pump), ("fan", fan)):
            if setting is None:
                continue
            try:
                if isinstance(setting, int):
                    self._driver.set_fixed_speed(channel, setting)
                    log.info("cooling: %s fixed at %d %%", channel, setting)
                else:
                    self._driver.set_speed_profile(channel, list(setting))
                    curve = ", ".join(f"{t:g}°C→{d}%" for t, d in setting)
                    log.info("cooling: %s curve applied (%s)", channel, curve)
            except Exception as exc:
                log.warning("applying the %s cooling setting failed: %s", channel, exc)

    # --------------------------------------------------------------- uploads

    def show_gif(self, path: Path) -> bool:
        """Upload *path* to the LCD. Returns False on (survivable) failure."""
        if self._driver is None:
            raise DeviceError("not connected")
        size = path.stat().st_size
        max_bytes = int(self._cfg.max_upload_megabytes * 1024 * 1024)
        if size > max_bytes:
            log.error("refusing to upload %s: %.1f MB exceeds the %.1f MB safety "
                      "limit", path.name, size / 2**20, self._cfg.max_upload_megabytes)
            return False

        # proactive: clear the image memory *before* the device would start
        # rejecting uploads (reactive recovery below stays as a safety net)
        memory_budget = int(self._cfg.image_memory_megabytes * 1024 * 1024)
        if self._bytes_since_cleanup and self._bytes_since_cleanup + size > memory_budget:
            log.info("image memory budget reached (%.1f MB uploaded), clearing "
                     "proactively", self._bytes_since_cleanup / 2**20)
            self.clear_image_memory()

        self._pace()
        retries = self._cfg.upload_retries
        for attempt in range(1, retries + 1):
            try:
                device_errors = self._try_upload(path)
            except Exception as exc:
                log.warning("upload attempt %d/%d failed: %s", attempt, retries, exc)
                if attempt < retries:
                    self._reconnect()  # DeviceNotFound/-InBootloader propagate
                    time.sleep(1.0)
                continue
            if not device_errors:
                self._last_upload = time.monotonic()
                self._bytes_since_cleanup += size
                self._last_upload_size = size
                return True
            log.warning("upload attempt %d/%d rejected by the device: %s",
                        attempt, retries, "; ".join(device_errors))
            if attempt < retries:
                self.clear_image_memory(full=True)
                time.sleep(1.0)
        self._last_upload = time.monotonic()
        return False

    def _try_upload(self, path: Path) -> list[str]:
        """One upload attempt; returns firmware errors the driver only logged."""
        from .driver_patch import BucketSetupRefused
        watcher = _DriverErrorWatcher()
        liquidctl_logger = logging.getLogger("liquidctl")
        liquidctl_logger.addHandler(watcher)
        try:
            self._driver.set_screen("lcd", "gif", str(path))
        except BucketSetupRefused as exc:
            # the patched driver aborted before streaming any data
            watcher.errors.append(str(exc))
        finally:
            liquidctl_logger.removeHandler(watcher)
        return watcher.errors

    def clear_image_memory(self, full: bool = False) -> None:
        """Free the device's image memory (16 buckets fill up after a few
        uploads; a full memory makes the firmware reject uploads).

        The default is a *soft* clear (patched driver only): inactive
        buckets are deleted while the displayed image keeps running — no
        visible flash. ``full=True`` (or the stock driver) clears every
        bucket, which briefly switches the LCD to the firmware liquid view.
        """
        if not full:
            soft = getattr(self._driver, "soft_clear_inactive", None)
            if soft is not None:
                try:
                    soft()
                    # the displayed bucket survives, keep it on the books
                    self._bytes_since_cleanup = self._last_upload_size
                    log.info("cleared inactive image buckets (no visible flash)")
                    return
                except Exception as exc:
                    log.warning("soft bucket clear failed (%s), clearing fully", exc)
        self._bytes_since_cleanup = 0
        cleanup = getattr(self._driver, "_delete_all_buckets", None)
        if cleanup is None:
            log.info("driver offers no bucket cleanup, reconnecting instead")
            self._reconnect()
            return
        log.info("clearing the device image memory")
        try:
            cleanup()
        except Exception as exc:
            log.warning("clearing image memory failed (%s), reconnecting", exc)
            self._reconnect()

    def _pace(self) -> None:
        elapsed = time.monotonic() - self._last_upload
        wait = self.MIN_UPLOAD_INTERVAL - elapsed
        if wait > 0:
            log.debug("pacing uploads: sleeping %.1f s", wait)
            time.sleep(wait)
