"""Periodic upload-health summary.

The 2024 Elite firmware refuses a bucket setup every so often (recovered
invisibly by the patched driver). The absolute rate is the single best
signal for whether a pacing/config change actually eased the firmware —
so this accumulator counts uploads and refusals over a rolling window and
logs one line per window. Read it straight from the journal:

    journalctl -u kraken-lcd | grep 'upload health'

instead of eyeballing individual "bucket setup refused" lines.
"""

import logging
import time

log = logging.getLogger(__name__)

WINDOW_SECONDS = 3600.0  # one summary line per hour of uploads


class UploadMonitor:
    def __init__(self, window_seconds: float = WINDOW_SECONDS,
                 clock=time.monotonic) -> None:
        self._window = window_seconds
        self._clock = clock
        self._reset()

    def _reset(self) -> None:
        self._start = self._clock()
        self._uploads = 0
        self._refusals = 0
        self._failures = 0

    def record(self, *, succeeded: bool, refusals: int) -> None:
        """Account one tile upload and how many firmware refusals it took."""
        self._uploads += 1
        self._refusals += max(0, refusals)
        if not succeeded:
            self._failures += 1
        if self._clock() - self._start >= self._window:
            self._emit()
            self._reset()

    def flush(self) -> None:
        """Emit the partial window now (e.g. on shutdown) and start over.

        Captures the crucial final stretch before a device wedges, which the
        next scheduled summary would otherwise never reach."""
        if self._uploads:
            self._emit()
        self._reset()

    def _emit(self) -> None:
        minutes = (self._clock() - self._start) / 60.0
        rate = 100.0 * self._refusals / self._uploads if self._uploads else 0.0
        log.info("upload health (last %.0f min): %d uploads, %d bucket "
                 "refusals (%.1f%%), %d failed",
                 minutes, self._uploads, self._refusals, rate, self._failures)
