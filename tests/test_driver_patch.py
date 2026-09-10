"""Tests for the patched KrakenZ3 driver.

The patched class is exercised without hardware: an instance is created via
``object.__new__`` and all transport-level private methods are replaced by
recorders. The pure bucket-placement logic (finding buckets, computing
offsets) runs for real.
"""

import pytest

from kraken_lcd.driver_patch import BucketSetupRefused, find_patched_kraken, patched_driver_class

# The patch deliberately stands down on liquidctl versions that do not
# satisfy the upstream contract (kraken_lcd.upstream). When that happens —
# e.g. after upstream fixes the bucket handling — these tests are moot, not
# broken. test_upstream.py fails instead of skipping on releases we claim
# to have verified.
try:
    patched_driver_class()
except RuntimeError as exc:
    pytest.skip(f"driver patch inactive with this liquidctl: {exc}",
                allow_module_level=True)


def _empty_bucket_response():
    return [0] * 64


def _occupied_bucket_response(index, offset_kb, size_kb):
    response = [0] * 64
    response[15] = index
    response[16] = index + 1
    response[17], response[18] = list(offset_kb.to_bytes(2, "little"))
    response[19], response[20] = list(size_kb.to_bytes(2, "little"))
    response[21] = 0x1
    return response


class _Harness:
    """Patched driver instance with recorded transport calls."""

    def __init__(self, setup_results=(True,), switch_ok=True, occupied=()):
        cls = patched_driver_class()
        self.driver = object.__new__(cls)
        d = self.driver
        d.bulk_device = object()
        d.bulk_buffer_size = 512
        self.bulk_writes = []
        self.setup_calls = []
        self.switch_calls = []
        self.deleted = []
        self.full_clears = 0
        self._setup_results = list(setup_results)

        buckets = {i: _empty_bucket_response() for i in range(16)}
        for index, offset_kb, size_kb in occupied:
            buckets[index] = _occupied_bucket_response(index, offset_kb, size_kb)

        d._write = lambda data: None
        d._write_then_read = lambda data: [0] * 64
        d._bulk_write = self.bulk_writes.append
        d._query_buckets = lambda: buckets
        d._delete_bucket = lambda i: self.deleted.append(i) or True

        def fake_setup(start, end, offset, size):
            self.setup_calls.append((start, end, list(offset), list(size)))
            return self._setup_results.pop(0) if self._setup_results else True

        d._setup_bucket = fake_setup

        def fake_switch(index, mode=0x4):
            self.switch_calls.append((index, mode))
            if switch_ok:
                d._active_bucket = index if mode == 0x4 else None
            return switch_ok

        d._switch_bucket = fake_switch

        def fake_full_clear():
            self.full_clears += 1
            d._active_bucket = None

        d._delete_all_buckets = fake_full_clear

    def send(self, kilobytes=100):
        self.driver._send_data(bytes(kilobytes * 1024), [0x1] * 8)


def test_patched_class_is_a_kraken_subclass():
    from liquidctl.driver.kraken3 import KrakenZ3
    assert issubclass(patched_driver_class(), KrakenZ3)


def test_happy_path_streams_data_and_switches():
    h = _Harness()
    h.send(kilobytes=100)
    assert len(h.setup_calls) == 1
    assert len(h.bulk_writes) > 1  # header + data chunks
    assert h.switch_calls == [(0, 0x4)]
    assert h.full_clears == 0


def test_setup_refusal_clears_and_retries_before_streaming():
    h = _Harness(setup_results=(False, True))
    h.send()
    assert h.full_clears == 1
    assert len(h.setup_calls) == 2
    assert h.setup_calls[1][0] == 0  # retry lands at bucket 0
    assert h.setup_calls[1][2] == [0, 0]  # ... at memory offset 0
    assert len(h.bulk_writes) > 0  # upload went through after the retry


def test_double_refusal_raises_without_streaming_any_data():
    h = _Harness(setup_results=(False, False))
    with pytest.raises(BucketSetupRefused):
        h.send()
    assert h.bulk_writes == []  # unlike stock: nothing streamed into nowhere
    assert h.switch_calls == []


def test_failed_switch_raises():
    h = _Harness(switch_ok=False)
    with pytest.raises(BucketSetupRefused):
        h.send()


def test_clean_upload_leaves_the_refusal_flag_false():
    h = _Harness()
    h.send()
    assert h.driver.last_upload_refused is False


def test_recovered_refusal_sets_the_flag():
    h = _Harness(setup_results=(False, True))
    h.send()
    assert h.driver.last_upload_refused is True


def test_refusal_flag_is_reset_at_the_start_of_each_upload():
    h = _Harness(setup_results=(False, True, True))
    h.send()
    assert h.driver.last_upload_refused is True
    h.send()  # a clean second upload must clear the stale flag
    assert h.driver.last_upload_refused is False


def test_second_upload_lands_after_the_first_bucket():
    # bucket 0 holds 4000 KB at offset 0 -> the next upload must be placed
    # in bucket 1 at offset 4000
    h = _Harness(occupied=((0, 0, 4000),))
    h.send(kilobytes=1000)
    start, _end, offset, _size = h.setup_calls[0]
    assert start == 1
    assert int.from_bytes(bytes(offset), "little") == 4000


def test_soft_clear_deletes_only_occupied_inactive_buckets():
    h = _Harness(occupied=((0, 0, 1000), (3, 1000, 500), (7, 1500, 500)))
    h.driver._active_bucket = 3
    h.driver.soft_clear_inactive()
    assert h.deleted == [0, 7]  # active kept, empty buckets never touched


def test_soft_clear_without_active_bucket_deletes_all_occupied():
    h = _Harness(occupied=((1, 0, 1000), (5, 1000, 500)))
    h.driver._active_bucket = None
    h.driver.soft_clear_inactive()
    assert h.deleted == [1, 5]


def test_soft_clear_on_empty_memory_sends_no_delete_commands():
    h = _Harness()
    h.driver._active_bucket = None
    h.driver.soft_clear_inactive()
    assert h.deleted == []


def test_real_switch_bucket_tracks_the_active_bucket():
    # exercises the actual patched _switch_bucket (transport faked)
    driver = object.__new__(patched_driver_class())
    ok_response = [0] * 64
    ok_response[14] = 0x1
    driver._write_then_read = lambda data: ok_response
    assert driver._switch_bucket(5) is True
    assert driver._active_bucket == 5
    assert driver._switch_bucket(0, 2) is True  # 0x2 = liquid view
    assert driver._active_bucket is None


def test_real_delete_all_buckets_resets_tracking():
    driver = object.__new__(patched_driver_class())
    ok_response = [0] * 64
    ok_response[14] = 0x1
    driver._write_then_read = lambda data: ok_response
    deleted = []
    driver._delete_bucket = lambda i: deleted.append(i) or True
    driver._active_bucket = 7
    driver._delete_all_buckets()
    assert driver._active_bucket is None
    assert deleted == list(range(16))


def test_find_patched_kraken_returns_none_without_hardware(monkeypatch):
    import liquidctl
    monkeypatch.setattr(liquidctl, "find_liquidctl_devices", lambda **kw: iter([]))
    assert find_patched_kraken() is None


def test_find_patched_kraken_upgrades_the_stock_instance(monkeypatch):
    import liquidctl
    from liquidctl.driver.kraken3 import KrakenZ3
    stock = object.__new__(KrakenZ3)
    stock._description = "NZXT Kraken 2024 Elite RGB"
    monkeypatch.setattr(liquidctl, "find_liquidctl_devices",
                        lambda **kw: iter([stock]))
    upgraded = find_patched_kraken()
    assert upgraded is stock
    assert type(upgraded) is patched_driver_class()
    assert upgraded._active_bucket is None


def test_find_patched_kraken_rejects_unknown_driver_classes(monkeypatch):
    import liquidctl
    from liquidctl.driver.kraken3 import KrakenZ3

    class FutureKraken(KrakenZ3):
        pass

    stranger = object.__new__(FutureKraken)
    stranger._description = "NZXT Kraken 2026"
    monkeypatch.setattr(liquidctl, "find_liquidctl_devices",
                        lambda **kw: iter([stranger]))
    with pytest.raises(RuntimeError):
        find_patched_kraken()


def test_patch_refuses_to_build_on_an_incompatible_liquidctl(monkeypatch):
    from kraken_lcd import driver_patch, upstream
    monkeypatch.setattr(driver_patch, "_patched_cls", None)
    monkeypatch.setattr(
        upstream, "installed",
        lambda: upstream.Compatibility("9.9.9", ("KrakenZ3._send_data changed upstream",)))
    with pytest.raises(RuntimeError, match="_send_data changed upstream"):
        driver_patch.patched_driver_class()
    with pytest.raises(RuntimeError):
        driver_patch.find_patched_kraken()  # never reaches the USB bus


def test_patch_class_is_built_once(monkeypatch):
    from kraken_lcd import driver_patch, upstream
    monkeypatch.setattr(driver_patch, "_patched_cls", None)
    calls = []
    real = upstream.installed()
    monkeypatch.setattr(upstream, "installed", lambda: calls.append(1) or real)
    first = driver_patch.patched_driver_class()
    assert driver_patch.patched_driver_class() is first
    assert calls == [1]
