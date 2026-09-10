import logging

from kraken_lcd.monitor import UploadMonitor

_LOGGER = "kraken_lcd.monitor"


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def test_no_summary_before_the_window_elapses(caplog):
    clock = _Clock()
    mon = UploadMonitor(window_seconds=3600, clock=clock)
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        for _ in range(20):
            mon.record(succeeded=True, refusals=0)
            clock.advance(20)  # 20 uploads over ~7 min, well inside the hour
    assert "upload health" not in caplog.text


def test_summary_emitted_once_the_window_is_full(caplog):
    clock = _Clock()
    mon = UploadMonitor(window_seconds=100, clock=clock)
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        mon.record(succeeded=True, refusals=1)   # t=0
        clock.advance(40)
        mon.record(succeeded=True, refusals=0)   # t=40
        clock.advance(40)
        mon.record(succeeded=True, refusals=0)   # t=80
        clock.advance(40)
        mon.record(succeeded=False, refusals=0)  # t=120 -> window full, emit
    assert "upload health" in caplog.text
    assert "4 uploads" in caplog.text
    assert "1 bucket refusals (25.0%)" in caplog.text
    assert "1 failed" in caplog.text


def test_window_resets_after_a_summary(caplog):
    clock = _Clock()
    mon = UploadMonitor(window_seconds=100, clock=clock)
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        mon.record(succeeded=True, refusals=5)   # t=0
        clock.advance(150)
        mon.record(succeeded=True, refusals=0)   # emit: 2 uploads, 5 refusals
        caplog.clear()
        clock.advance(150)
        mon.record(succeeded=True, refusals=0)   # emit: fresh 1 upload, 0 refusals
    assert "1 uploads" in caplog.text
    assert "0 bucket refusals (0.0%)" in caplog.text


def test_flush_emits_the_partial_window(caplog):
    clock = _Clock()
    mon = UploadMonitor(window_seconds=3600, clock=clock)
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        mon.record(succeeded=True, refusals=2)
        mon.record(succeeded=True, refusals=0)
        mon.flush()
    assert "upload health" in caplog.text
    assert "2 uploads" in caplog.text
    assert "2 bucket refusals (100.0%)" in caplog.text


def test_flush_without_uploads_is_silent(caplog):
    mon = UploadMonitor(clock=_Clock())
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        mon.flush()
    assert "upload health" not in caplog.text


def test_flush_resets_so_the_next_window_starts_clean(caplog):
    clock = _Clock()
    mon = UploadMonitor(window_seconds=3600, clock=clock)
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        mon.record(succeeded=True, refusals=9)
        mon.flush()
        caplog.clear()
        mon.record(succeeded=True, refusals=0)
        mon.flush()
    assert "1 uploads" in caplog.text
    assert "0 bucket refusals (0.0%)" in caplog.text


def test_negative_refusals_are_ignored(caplog):
    # defensive: a stale/reset driver counter must never inflate the rate
    clock = _Clock()
    mon = UploadMonitor(window_seconds=3600, clock=clock)
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        mon.record(succeeded=True, refusals=-3)
        mon.flush()
    assert "0 bucket refusals (0.0%)" in caplog.text
