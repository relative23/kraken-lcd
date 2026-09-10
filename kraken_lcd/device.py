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
from .monitor import UploadMonitor

log = logging.getLogger(__name__)

NZXT_VENDOR_ID = 0x1E71
BOOTLOADER_PRODUCT_ID = 0x3011

BOOTLOADER_RECOVERY = (
    "Kraken is stuck in bootloader mode (USB 1e71:3011). Recovery: shut the "
    "machine down, switch the PSU off (or unplug) for ~30 seconds, then boot. "
    "A plain reboot is NOT enough — the device keeps standby power."
)

# Exit status for conditions a restart cannot fix (device in its bootloader,
# firmware that cannot show GIFs): the systemd unit lists it in
# RestartPreventExitStatus so the service is not hammered with retries.
EXIT_NO_RESTART = 78
EXIT_BOOTLOADER = EXIT_NO_RESTART  # historical name

# supported product IDs and the liquidctl release that introduced them
SUPPORTED_PRODUCTS = {
    0x3008: ("Kraken Z53/Z63/Z73", "1.13"),
    0x300C: ("Kraken 2023 Elite", "1.14"),
    0x300E: ("Kraken 2023", "1.14"),
    0x3012: ("Kraken 2024 Elite RGB", "1.15"),
    0x3014: ("Kraken 2024 Plus", "1.16"),
}


class DeviceError(Exception):
    """Base class for device problems."""


class DeviceNotFound(DeviceError):
    pass


class DeviceInBootloader(DeviceError):
    pass


class DeviceUnsupported(DeviceError):
    """The device or its firmware cannot do what the daemon needs (e.g. the
    Kraken 2023 on firmware 2.x cannot show GIFs through liquidctl).
    Retrying or restarting will not change that."""


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


_patch_fallback_logged = False


def _find_kraken(use_patch: bool = True):
    global _patch_fallback_logged
    if use_patch:
        from .driver_patch import find_patched_kraken
        try:
            return find_patched_kraken()
        except RuntimeError as exc:
            # once per process, not on every reconnect
            level = logging.DEBUG if _patch_fallback_logged else logging.WARNING
            _patch_fallback_logged = True
            log.log(level, "driver patch disabled itself — using the stock "
                    "driver: uploads are no longer verified before streaming "
                    "and memory cleanup briefly flashes the LCD. Reason: %s "
                    "(details: kraken-lcd doctor)", exc)
    return _find_stock_kraken()


def _unsupported_errors() -> tuple[type[Exception], ...]:
    """liquidctl's 'this device/firmware cannot do that' exception types."""
    try:
        import liquidctl.error as errors
    except Exception:
        return ()
    return tuple(cls for cls in (getattr(errors, "NotSupportedByDriver", None),
                                 getattr(errors, "NotSupportedByDevice", None))
                 if isinstance(cls, type))


def _firmware_version(status) -> str | None:
    """Extract the firmware version from an initialize()/get_status() list."""
    try:
        for name, value, _unit in status or ():
            if "firmware" in str(name).lower():
                return str(value)
    except (TypeError, ValueError):
        pass
    return None


def _bootloader_present() -> bool:
    try:
        import usb.core
        return usb.core.find(idVendor=NZXT_VENDOR_ID,
                             idProduct=BOOTLOADER_PRODUCT_ID) is not None
    except Exception:
        return False


class _DriverErrorWatcher(logging.Handler):
    """Collects ERROR records from liquidctl during one operation.

    Stock-driver fallback: the stock KrakenZ3 does not raise on
    firmware-level upload failures ("Failed to setup bucket ..."), it only
    logs them, so scraping the log is the only way to notice that an
    upload did not reach the screen. The patched driver raises
    ``BucketSetupRefused`` instead, which ``_try_upload`` maps onto the
    same error list — the rest of the device layer is driver-agnostic.
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
    # circuit breaker: this many consecutive status-read *errors* (one full
    # read_status() worth) mark the transport as sick — reconnect before the
    # next upload instead of streaming megabytes into an unresponsive device
    MAX_STATUS_FAILURES = 3

    def __init__(self, cfg: DeviceConfig, finder=None, monitor=None) -> None:
        self._cfg = cfg
        self._finder = finder or (lambda: _find_kraken(cfg.driver_patch))
        self._driver = None
        self._last_upload = 0.0
        self._bytes_since_cleanup = 0
        self._last_upload_size = 0
        self._status_failures = 0
        self._monitor = monitor or UploadMonitor()
        self._firmware: str | None = None

    @property
    def description(self) -> str:
        return self._driver.description if self._driver else "not connected"

    @property
    def firmware_version(self) -> str | None:
        """Firmware version reported by the device at connect, if any."""
        return self._firmware

    @property
    def driver_class(self) -> str:
        """Name of the liquidctl driver class in use (PatchedKrakenZ3 or
        the stock KrakenZ3); 'none' while disconnected."""
        return type(self._driver).__name__ if self._driver else "none"

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
            supported = ", ".join(f"1e71:{pid:04x} ({name}, liquidctl >= {since})"
                                  for pid, (name, since) in SUPPORTED_PRODUCTS.items())
            raise DeviceNotFound(
                f"no NZXT Kraken with an LCD found; supported: {supported} — "
                f"check `lsusb | grep 1e71` and `kraken-lcd doctor`")
        driver.connect()
        self._driver = driver
        try:
            self._firmware = _firmware_version(driver.initialize())
            driver.get_status()  # first read after init is often bogus; discard
        except Exception as exc:
            log.warning("initialize() failed, continuing anyway: %s", exc)
        self._status_failures = 0
        log.info("connected to %s (firmware %s, driver %s)", driver.description,
                 self._firmware or "unknown", self.driver_class)

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
            self._status_failures += 1
            log.warning("reading device status failed: %s", exc)
        else:
            self._status_failures = 0
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

        # circuit breaker: a device whose status endpoint stopped answering
        # must not be fed another multi-megabyte stream — reconnect first,
        # which also notices a device that fell into its bootloader
        if self._status_failures >= self.MAX_STATUS_FAILURES:
            log.warning("%d consecutive status read failures — reconnecting "
                        "before the next upload", self._status_failures)
            self._reconnect()  # DeviceNotFound/-InBootloader propagate

        # proactive: clear the image memory *before* the device would start
        # rejecting uploads (reactive recovery below stays as a safety net)
        memory_budget = int(self._cfg.image_memory_megabytes * 1024 * 1024)
        if self._bytes_since_cleanup and self._bytes_since_cleanup + size > memory_budget:
            log.info("image memory budget reached (%.1f MB uploaded), clearing "
                     "proactively", self._bytes_since_cleanup / 2**20)
            self.clear_image_memory()

        self._pace()
        retries = self._cfg.upload_retries
        refusals = 0
        for attempt in range(1, retries + 1):
            try:
                device_errors = self._try_upload(path)
            except DeviceError:
                raise  # unsupported firmware: retrying cannot help
            except Exception as exc:
                refusals += self._took_refusal()
                log.warning("upload attempt %d/%d failed: %s", attempt, retries, exc)
                if attempt < retries:
                    self._reconnect()  # DeviceNotFound/-InBootloader propagate
                    time.sleep(1.0)
                continue
            refusals += self._took_refusal()
            if not device_errors:
                self._last_upload = time.monotonic()
                self._bytes_since_cleanup += size
                self._last_upload_size = size
                self._monitor.record(succeeded=True, refusals=refusals)
                return True
            log.warning("upload attempt %d/%d rejected by the device: %s",
                        attempt, retries, "; ".join(device_errors))
            if attempt < retries:
                self.clear_image_memory(full=True)
                time.sleep(1.0)
        self._last_upload = time.monotonic()
        self._monitor.record(succeeded=False, refusals=refusals)
        return False

    def _took_refusal(self) -> int:
        """1 if the just-finished attempt triggered a firmware bucket refusal.

        The patched driver recovers a single refusal invisibly (retry at
        offset 0); reading its flag is the only way to count how often the
        firmware balks. Read before any reconnect swaps the driver out."""
        return int(bool(getattr(self._driver, "last_upload_refused", False)))

    def flush_upload_stats(self) -> None:
        """Log the pending upload-health summary (called on shutdown)."""
        self._monitor.flush()

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
        except _unsupported_errors() as exc:
            # e.g. Kraken 2023 on firmware 2.x: liquidctl cannot upload GIFs
            # (liquidctl #631); no retry, reconnect or restart will fix this
            raise DeviceUnsupported(
                f"{self.description} (firmware {self._firmware or 'unknown'}) "
                f"cannot show GIFs through liquidctl: {exc}") from exc
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
