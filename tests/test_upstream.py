"""Tests for the liquidctl upstream contract behind the driver patch."""

import pytest

from kraken_lcd import upstream
from kraken_lcd.upstream import CONTRACT, Compatibility, check, fingerprint, installed

# --- fingerprint: what may change upstream without disabling the patch ---

def _original(self, data, bulkInfo):
    """Docstring one."""
    header = [0x12, 0xFA] + bulkInfo  # trailing comment
    if not self._setup_bucket(0, 1, [0, 0], [1, 0]):
        log.error("Failed to setup bucket for data transfer")  # noqa: F821

    self._bulk_write(header)
    return len(data)


def _reformatted_with_other_comments(self, data, bulkInfo):
    """A completely different docstring.

    Spanning several lines.
    """
    # a new leading comment
    header = [
        0x12,
        0xFA
    ] + bulkInfo
    if not self._setup_bucket(
        0, 1, [0, 0], [1, 0]
    ):
        log.error("Failed to setup bucket for data transfer")  # noqa: F821
    self._bulk_write(header)
    return len(data)


def _without_docstring(self, data, bulkInfo):
    header = [0x12, 0xFA] + bulkInfo
    if not self._setup_bucket(0, 1, [0, 0], [1, 0]):
        log.error("Failed to setup bucket for data transfer")  # noqa: F821
    self._bulk_write(header)
    return len(data)


def _logic_changed(self, data, bulkInfo):
    """Docstring one."""
    header = [0x12, 0xFA] + bulkInfo
    if not self._setup_bucket(0, 1, [0, 0], [1, 0]):
        raise RuntimeError("Failed to setup bucket for data transfer")
    self._bulk_write(header)
    return len(data)


def _statement_moved_out_of_block(self, data, bulkInfo):
    """Docstring one."""
    header = [0x12, 0xFA] + bulkInfo
    if not self._setup_bucket(0, 1, [0, 0], [1, 0]):
        log.error("Failed to setup bucket for data transfer")  # noqa: F821
        self._bulk_write(header)
    return len(data)


def test_fingerprint_ignores_docstrings_comments_and_formatting():
    assert fingerprint(_original) == fingerprint(_reformatted_with_other_comments)
    assert fingerprint(_original) == fingerprint(_without_docstring)


def test_fingerprint_changes_when_the_logic_changes():
    assert fingerprint(_original) != fingerprint(_logic_changed)


def test_fingerprint_sees_block_structure():
    # same tokens, different indentation: a real behavioural change
    assert fingerprint(_original) != fingerprint(_statement_moved_out_of_block)


def test_fingerprint_is_short_and_stable():
    assert len(fingerprint(_original)) == 12
    assert fingerprint(_original) == fingerprint(_original)


# --- check(): the verdict on a driver class ---

class _StubKrakenZ3:
    """KrakenZ3's shape (1.14+): every internal of the contract, with
    source available to inspect, so fingerprints can be computed."""

    def __init__(self, device, description, speed_channels, color_channels,
                 bulk_buffer_size, lcd_resolution, **kwargs):
        pass

    def _send_data(self, data, bulkInfo):
        return None

    def _setup_bucket(self, startBucketIndex, endBucketIndex, startingMemoryAddress, memorySize):
        return True

    def _switch_bucket(self, bucketIndex, mode=4):
        return True

    def _delete_all_buckets(self):
        return None

    def _delete_bucket(self, bucketIndex):
        return True

    def _query_buckets(self):
        return {}

    def _find_next_unoccupied_bucket(self, buckets):
        return 0

    def _prepare_bucket(self, bucketIndex, bucketFilled):
        return bucketIndex

    def _get_bucket_memory_offset(self, buckets, bucketIndex, dataSize):
        return [0, 0]

    def _bulk_write(self, data):
        return None

    def _write(self, data):
        return None

    def _write_then_read(self, data):
        return [0] * 64


def _fake_driver_class():
    """A fresh copy of the stub class (tests mutate it)."""
    body = {name: value for name, value in vars(_StubKrakenZ3).items()
            if callable(value)}
    return type("KrakenZ3", (), body)


def test_stub_covers_the_whole_contract():
    assert {i.name for i in CONTRACT} <= set(vars(_StubKrakenZ3))


def _fingerprints_of(cls):
    """Contract copy whose fingerprints match *cls* (to isolate other checks)."""
    return tuple(upstream.Internal(i.name, i.signature,
                                   fingerprint(getattr(cls, i.name)), i.role)
                 for i in CONTRACT)


def test_check_accepts_a_matching_class(monkeypatch):
    cls = _fake_driver_class()
    monkeypatch.setattr(upstream, "CONTRACT", _fingerprints_of(cls))
    result = check(cls, "9.9.9")
    assert result.compatible
    assert result.verified_release is False
    assert "every internal matches" in result.summary()


def test_check_reports_verified_releases(monkeypatch):
    cls = _fake_driver_class()
    monkeypatch.setattr(upstream, "CONTRACT", _fingerprints_of(cls))
    result = check(cls, upstream.VERIFIED_RELEASES[0])
    assert result.compatible and result.verified_release
    assert "verified release" in result.summary()


def test_check_flags_a_missing_method(monkeypatch):
    cls = _fake_driver_class()
    monkeypatch.setattr(upstream, "CONTRACT", _fingerprints_of(cls))
    del cls._send_data
    result = check(cls, "1.15.0")
    assert not result.compatible
    assert result.problems == ("KrakenZ3._send_data is gone",)


def test_check_flags_a_changed_signature(monkeypatch):
    cls = _fake_driver_class()
    monkeypatch.setattr(upstream, "CONTRACT", _fingerprints_of(cls))
    cls._switch_bucket = lambda self, bucketIndex, mode=4, force=False: None
    result = check(cls, "1.15.0")
    assert len(result.problems) == 1
    assert "KrakenZ3._switch_bucket signature changed" in result.problems[0]


def test_check_flags_a_changed_body(monkeypatch):
    cls = _fake_driver_class()
    monkeypatch.setattr(upstream, "CONTRACT", _fingerprints_of(cls))
    cls._send_data = lambda self, data, bulkInfo: 42
    result = check(cls, "1.15.0")
    assert len(result.problems) == 1
    assert "KrakenZ3._send_data changed upstream" in result.problems[0]


def test_check_flags_pre_1_14_constructor(monkeypatch):
    cls = _fake_driver_class()
    monkeypatch.setattr(upstream, "CONTRACT", _fingerprints_of(cls))

    def old_init(self, device, description, speed_channels, color_channels, **kw):
        pass

    cls.__init__ = old_init
    result = check(cls, "1.13.0")
    assert [p for p in result.problems if "bulk_buffer_size" in p]
    assert [p for p in result.problems if "lcd_resolution" in p]


def test_check_stands_down_without_source(monkeypatch):
    cls = _fake_driver_class()
    monkeypatch.setattr(upstream, "CONTRACT", _fingerprints_of(cls))

    def no_source(function):
        raise OSError("could not get source code")

    monkeypatch.setattr(upstream, "fingerprint", no_source)
    result = check(cls, "1.15.0")
    assert not result.compatible
    assert all("source not available" in p for p in result.problems)
    assert len(result.problems) == len(CONTRACT)


def test_summary_lists_every_problem():
    result = Compatibility("1.99.0", ("a is gone", "b changed"))
    assert result.summary() == "liquidctl 1.99.0: a is gone; b changed"


# --- the installed liquidctl ---

def test_installed_is_cached():
    assert installed() is installed()


def test_installed_reports_the_real_version():
    import liquidctl
    assert installed().liquidctl_version == liquidctl.__version__


def test_verified_release_satisfies_the_contract():
    """On a liquidctl release we claim to have verified, the patch must be
    able to activate — a failure here means the fingerprints in CONTRACT
    are stale for that release (see docs/liquidctl-compatibility.md)."""
    result = installed()
    if not result.verified_release:
        pytest.skip(f"liquidctl {result.liquidctl_version} is not a verified "
                    f"release; verdict: {result.summary()}")
    assert result.compatible, result.summary()
