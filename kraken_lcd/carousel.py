"""Main loop: read sensors, render the current tile, upload, wait, repeat."""

import logging
import signal
import threading
import time
from dataclasses import replace

from .cache import RenderCache
from .config import Config
from .device import DeviceError, DeviceInBootloader, KrakenDevice
from .render import render_gif
from .screens import Screen, build_screens, cache_key, effective_render_config
from .sdnotify import SystemdNotifier
from .sensors import SensorReader

log = logging.getLogger(__name__)

MAX_CONSECUTIVE_FAILURES = 3
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
        """Blocks until stopped. Raises DeviceError when the device gives up."""
        cfg = self._cfg
        if not self._connect_with_retry():
            return  # stop was requested before a connection came up
        try:
            self._resolve_render_size()
            # start from empty image memory (invisible: the LCD is still on
            # the firmware liquid screen at this point)
            self._device.clear_image_memory()
            self._device.apply_cooling(cfg.cooling.pump, cfg.cooling.fan)
            self._device.set_brightness(cfg.carousel.brightness)
            self._notifier.ready()
            log.info("carousel running: %s — %.0f s per tile",
                     ", ".join(cfg.carousel.screens), cfg.carousel.display_seconds)
            while not self._stop.is_set():
                self._one_cycle()
        finally:
            self._device.flush_upload_stats()
            self._device.reset_to_liquid()
            self._device.disconnect()

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
            if self._show_screen(self._screens[name]):
                shown += 1
                self._wait_and_prerender(names[(index + 1) % len(names)])
        if shown == 0:
            log.warning("no tile could be shown this cycle, retrying in %.0f s",
                        self._cfg.carousel.display_seconds)
            self._stop.wait(self._cfg.carousel.display_seconds)

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
        if self._device.show_gif(path):
            self._failures = 0
            log.info("tile %s: %s", screen.name,
                     " ".join(e.text for e in elements))
            return True
        self._failures += 1
        if self._failures >= MAX_CONSECUTIVE_FAILURES:
            raise DeviceError(
                f"{self._failures} consecutive upload failures — giving up")
        return False
