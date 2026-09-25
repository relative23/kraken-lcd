"""Recent sensor values for the chart screens, kept across restarts.

One point per minute at most, for the last 24 hours, in a small JSON file
next to the render cache. Losing it costs nothing but a shorter chart.
"""

import json
import logging
import os
import time
from pathlib import Path

from .sensors import SensorSnapshot

log = logging.getLogger(__name__)

SERIES = ("liquid", "cpu_load", "gpu_load", "cpu_temp")
SPAN_SECONDS = 24 * 3600
MIN_INTERVAL_SECONDS = 60.0
SAVE_INTERVAL_SECONDS = 600.0


def _values(snap: SensorSnapshot) -> list[float | None]:
    return [snap.liquid_temp, snap.cpu_load, snap.gpu_load, snap.cpu_temp]


class History:
    def __init__(self, path: Path | None) -> None:
        self._path = path
        self._points: list[list] = []  # [timestamp, *values]
        self._saved_at = 0.0
        self._load()

    def _load(self) -> None:
        if self._path is None or not self._path.is_file():
            return
        try:
            data = json.loads(self._path.read_text())
            points = [p for p in data.get("points", [])
                      if isinstance(p, list) and len(p) == 1 + len(SERIES)]
            self._points = sorted(points, key=lambda p: p[0])
        except (OSError, ValueError, AttributeError) as exc:
            log.warning("history %s unreadable, starting empty: %s", self._path, exc)

    def record(self, snap: SensorSnapshot, now: float | None = None) -> None:
        now = time.time() if now is None else now
        if self._points and now - self._points[-1][0] < MIN_INTERVAL_SECONDS:
            return
        values = _values(snap)
        if all(v is None for v in values):
            return
        self._points.append([now, *values])
        cutoff = now - SPAN_SECONDS
        while self._points and self._points[0][0] < cutoff:
            self._points.pop(0)
        if now - self._saved_at >= SAVE_INTERVAL_SECONDS:
            self.save(now)

    def save(self, now: float | None = None) -> None:
        if self._path is None:
            return
        self._saved_at = time.time() if now is None else now
        tmp = self._path.with_name(self._path.name + ".tmp")
        try:
            tmp.write_text(json.dumps({"version": 1, "series": SERIES,
                                       "points": self._points}))
            os.replace(tmp, self._path)
        except OSError as exc:
            log.warning("could not save history to %s: %s", self._path, exc)
            tmp.unlink(missing_ok=True)

    def series(self) -> dict[str, tuple[float, ...]]:
        """Each series without its gaps, oldest first."""
        return {name: tuple(p[i + 1] for p in self._points if p[i + 1] is not None)
                for i, name in enumerate(SERIES)}
