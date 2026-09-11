"""Main loop: read sensors, render the current tile, upload, wait, repeat."""

import logging
import signal
import threading
import time
from dataclasses import replace

from .cache import RenderCache
from .config import Config
from .device import DeviceError, DeviceInBootloader, DeviceUnsupported, KrakenDevice
from .render import render_gif
from .screens import Screen, build_screens, cache_key, effective_render_config
from .sdnotify import SystemdNotifier
from .sensors import SensorReader

log = logging.getLogger(__name__)

# upload failures in a row before the carousel stops pushing data and cools
# down; the device is disconnected meanwhile and the LCD handed back to the
# firmware screen
MAX_CONSECUTIVE_FAILURES = 3
# cooldown ladder in minutes: one step per consecutive cooldown, the last
# value repeats. Background: in 2026-08 a 2024 Elite stopped answering every
# command while still enumerated; the daemon gave up after 3 failures, exited
# and was restarted by systemd every ~3.6 min for five days (1931 restarts),
# each restart re-initializing and re-uploading against the hung device.
# Cooling down inside the process caps that at about one probe per hour.
COOLDOWN_MINUTES = (5.0, 15.0, 60.0)
# keep-alive cadence while waiting (the unit's WatchdogSec is 90 s)
WATCHDOG_PING_SECONDS = 30.0
# USB may still be enumerating at boot; retry the initial connect briefly
CONNECT_ATTEMPTS = 3
CONNECT_RETRY_SECONDS = 5.0
DEFAULT_RENDER_SIZE = 640  # when the device does not report its resolution


class Carousel:
    def __init__(self, cfg: Config, device: KrakenDevice,
                 sensors: SensorReader, cache: RenderCache,
                 notifier: SystemdNotifier | None = None) -> None:
        self._cfg = cfg
        self._device = device
        self._sensors = sensors
        self._cache = cache
        self._screens = build_screens(cfg.screen_styles)
        self._notifier = notifier or SystemdNotifier()
        self._stop = threading.Event()
        self._failures = 0
        self._cooldowns = 0  # consecutive cooldowns; reset by a successful upload
        # resolved after connect (render.size == 0 means: ask the device)
        self._render_cfg = cfg.render

    def request_stop(self, *_args) -> None:
        log.info("stop requested")
        self._notifier.stopping()
        self._stop.set()

    def install_signal_handlers(self) -> None:
        signal.signal(signal.SIGTERM, self.request_stop)
        signal.signal(signal.SIGINT, self.request_stop)

    def run(self) -> None:
        """Blocks until stopped.

        Raises DeviceError only for conditions a retry cannot fix (device
        in its bootloader, unsupported firmware) or when the initial
        connection never comes up. A device that fails or vanishes while
        running is handled by cooling down and reconnecting, not by
        exiting — an exit only turns the problem into a restart loop.
        """
        cfg = self._cfg
        if not self._connect_with_retry():
            return  # stop was requested before a connection came up
        try:
            self._resolve_render_size()
            self._after_connect()
            self._notifier.ready()
            log.info("carousel running: %s — %.0f s per tile",
                     ", ".join(cfg.carousel.screens), cfg.carousel.display_seconds)
            while not self._stop.is_set():
                self._one_cycle()
        finally:
            self._device.flush_upload_stats()
            self._device.reset_to_liquid()
            self._device.disconnect()

    def _after_connect(self) -> None:
        """Device setup after every (re)connect: empty image memory (invisible
        while the LCD is on the firmware screen), cooling, brightness."""
        cfg = self._cfg
        self._device.clear_image_memory()
        self._device.apply_cooling(cfg.cooling.pump, cfg.cooling.fan)
        self._device.set_brightness(cfg.carousel.brightness)

    def _resolve_render_size(self) -> None:
        """Turn render.size == 0 (auto) into the device's real resolution."""
        if self._cfg.render.size != 0:
            return
        resolution = self._device.lcd_resolution
        size = min(resolution) if resolution else DEFAULT_RENDER_SIZE
        self._render_cfg = replace(self._cfg.render, size=size)
        log.info("render size: %d px (%s)", size,
                 "reported by the device" if resolution else "fallback")

    def _connect_with_retry(self) -> bool:
        """Connect to the device, absorbing boot-time races.

        A detected bootloader is final (recovery needs a power cycle), so it
        is never retried. Returns False when a stop request arrived first.
        """
        for attempt in range(1, CONNECT_ATTEMPTS + 1):
            if self._stop.is_set():
                return False
            try:
                self._device.connect()
                return True
            except DeviceInBootloader:
                raise
            except DeviceError as exc:
                if attempt == CONNECT_ATTEMPTS:
                    raise
                log.warning("connect attempt %d/%d failed (%s), retrying in %.0f s",
                            attempt, CONNECT_ATTEMPTS, exc, CONNECT_RETRY_SECONDS)
                self._stop.wait(CONNECT_RETRY_SECONDS)
        return False

    def _one_cycle(self) -> None:
        names = self._cfg.carousel.screens
        shown = 0
        for index, name in enumerate(names):
            if self._stop.is_set():
                return
            self._notifier.watchdog()
            try:
                ok = self._show_screen(self._screens[name])
            except (DeviceInBootloader, DeviceUnsupported):
                raise  # final: a restart cannot help, exit code 78
            except DeviceError as exc:
                # e.g. the device vanished during a reconnect inside show_gif
                log.warning("device error while showing %s: %s", name, exc)
                self._failures = MAX_CONSECUTIVE_FAILURES
                ok = False
            if self._failures >= MAX_CONSECUTIVE_FAILURES:
                self._cooldown()
                return  # start a fresh cycle on the reconnected device
            if ok:
                shown += 1
                self._wait_and_prerender(names[(index + 1) % len(names)])
        if shown == 0:
            log.warning("no tile could be shown this cycle, retrying in %.0f s",
                        self._cfg.carousel.display_seconds)
            self._stop.wait(self._cfg.carousel.display_seconds)

    def _cooldown(self) -> None:
        """Stop pushing data to a device that keeps failing.

        Hands the LCD back to the firmware, disconnects, waits (5, 15, then
        60 min per consecutive cooldown) with the watchdog kept alive, and
        reconnects. A reconnect that fails starts the next, longer cooldown;
        a bootloader is final and propagates. Returns once the device is
        back or a stop was requested.
        """
        while not self._stop.is_set():
            self._cooldowns += 1
            minutes = COOLDOWN_MINUTES[min(self._cooldowns, len(COOLDOWN_MINUTES)) - 1]
            log.error("%d consecutive upload failures — cooling down for %.0f min "
                      "(cooldown #%d), the LCD is handed back to the firmware "
                      "meanwhile", self._failures, minutes, self._cooldowns)
            self._device.reset_to_liquid()  # best effort on a sick device
            self._device.disconnect()
            self._wait_with_watchdog(minutes * 60.0)
            if self._stop.is_set():
                return
            try:
                self._device.connect()
                # connect() tolerates a failing initialize(); a device that
                # is enumerated but answers nothing must not count as back
                if self._device.read_status().liquid_temp is None:
                    log.warning("device connected but does not answer status "
                                "reads, staying in cooldown")
                    continue
                self._after_connect()
            except (DeviceInBootloader, DeviceUnsupported):
                raise
            except DeviceError as exc:
                log.warning("reconnect after cooldown failed: %s", exc)
                continue
            self._failures = 0
            log.info("device is back after cooldown #%d, resuming the carousel",
                     self._cooldowns)
            return

    def _wait_with_watchdog(self, seconds: float) -> None:
        """Sleep (interruptible by a stop request) while pinging systemd."""
        deadline = time.monotonic() + seconds
        while not self._stop.is_set():
            self._notifier.watchdog()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            self._stop.wait(min(WATCHDOG_PING_SECONDS, remaining))

    def _wait_and_prerender(self, next_name: str) -> None:
        """Display wait for the current tile, with the *next* tile rendered
        at its start — the render happens while the current tile is visible
        and its duration is deducted from the wait, so the cadence stays
        constant and the next upload is (usually) a cache hit."""
        started = time.monotonic()
        try:
            self._render_tile(self._screens[next_name])
        except Exception:
            log.exception("prerendering tile %s failed", next_name)
        remaining = (self._cfg.carousel.display_seconds
                     - (time.monotonic() - started))
        if remaining > 0:
            self._stop.wait(remaining)

    def _render_tile(self, screen: Screen):
        """Fresh sensor snapshot -> cached (or newly rendered) GIF.

        Returns (path, elements); (None, None) when the tile's sensors are
        unavailable, (None, elements) when rendering failed.
        """
        status = self._device.read_status()
        snapshot = self._sensors.snapshot(liquid_temp=status.liquid_temp,
                                          pump_rpm=status.pump_rpm,
                                          fan_rpm=status.fan_rpm)
        elements = screen.build(snapshot, self._cfg.cache)
        if elements is None:
            return None, None
        background = self._cfg.assets_dir / screen.background
        render_cfg = effective_render_config(screen, self._render_cfg)
        budget = int(self._cfg.device.max_upload_megabytes * 1024 * 1024)
        key = cache_key(screen, elements, render_cfg, budget, background)
        path = self._cache.get_or_render(
            key, lambda out: render_gif(background, elements, out,
                                        render_cfg, budget_bytes=budget))
        return path, elements

    def _show_screen(self, screen: Screen) -> bool:
        path, elements = self._render_tile(screen)
        if path is None:
            if elements is None:
                log.debug("tile %s skipped: required sensors unavailable",
                          screen.name)
            return False
        if self._device.displayed_upload == path:
            # the exact same file is already on the LCD (single-tile
            # display, unchanged values): every skipped upload is one less
            # bucket write on a firmware that dislikes them
            log.debug("tile %s unchanged, upload skipped", screen.name)
            self._failures = 0
            return True
        if self._device.show_gif(path):
            self._failures = 0
            self._cooldowns = 0
            log.info("tile %s: %s", screen.name,
                     " ".join(e.text for e in elements))
            return True
        self._failures += 1
        return False
