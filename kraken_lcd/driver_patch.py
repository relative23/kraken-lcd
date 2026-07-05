"""A patched KrakenZ3 driver with 2024-Elite-safe bucket handling.

Stock liquidctl (verified in 1.15.0 and in upstream main as of 2026-07)
has two weaknesses in ``KrakenZ3._send_data`` that show on the 2024 Elite:

1. When the firmware refuses the bucket setup — which the 2024 Elite does
   sporadically even with plenty of free image memory — the stock driver
   only *logs* the error, then streams the whole multi-megabyte image into
   nowhere and finally fails the bucket switch: the LCD keeps showing the
   old image and the caller never learns about it.
   → ``PatchedKrakenZ3`` verifies the setup *before* streaming; on refusal
   it clears the image memory and retries once at offset 0; if that fails
   too it raises ``BucketSetupRefused`` instead of pretending success.

2. The only cleanup tool, ``_delete_all_buckets()``, switches the screen
   to the firmware liquid view first — a visible flash. For routine memory
   housekeeping that is unnecessary: deleting only the *inactive* buckets
   frees the memory while the displayed image keeps running.
   → ``soft_clear_inactive()`` does exactly that; the active bucket is
   tracked through ``_switch_bucket``.

The patch is deliberately defensive: it only activates when the installed
liquidctl still contains the known-broken code path (checked by source
inspection). On any mismatch — e.g. a future liquidctl release that fixes
or reworks ``_send_data`` — ``patched_driver_class()`` raises and the
caller falls back to the stock driver.

An upstream contribution based on this analysis is drafted in
``docs/UPSTREAM.md``.
"""

import inspect
import logging
import math

log = logging.getLogger(__name__)

_BUCKET_COUNT = 16

# marker of the known-broken upstream error handling; if it disappears,
# upstream changed (or fixed) _send_data and this patch must stand down
_STOCK_MARKER = "Failed to setup bucket for data transfer"

_REQUIRED_INTERNALS = (
    "_write", "_write_then_read", "_bulk_write", "_query_buckets",
    "_find_next_unoccupied_bucket", "_prepare_bucket",
    "_get_bucket_memory_offset", "_setup_bucket", "_switch_bucket",
    "_delete_bucket", "_delete_all_buckets",
)


class BucketSetupRefused(Exception):
    """The firmware refused the bucket setup / switch; the upload was
    aborted *before* any image data was streamed."""


_patched_cls = None


def patched_driver_class():
    """Build (once) and return the patched KrakenZ3 subclass.

    Raises when the installed liquidctl does not look like the version this
    patch was written against — callers must then use the stock driver.
    """
    global _patched_cls
    if _patched_cls is not None:
        return _patched_cls

    from liquidctl.driver.kraken3 import KrakenZ3

    for name in _REQUIRED_INTERNALS:
        if not hasattr(KrakenZ3, name):
            raise RuntimeError(f"KrakenZ3.{name} is gone — liquidctl changed")
    if _STOCK_MARKER not in inspect.getsource(KrakenZ3._send_data):
        raise RuntimeError("KrakenZ3._send_data changed upstream — "
                           "the bucket bug may already be fixed there")

    class PatchedKrakenZ3(KrakenZ3):
        """KrakenZ3 with verified bucket setup and flash-free cleanup."""

        _active_bucket: int | None = None

        def _switch_bucket(self, bucketIndex, mode=0x4):
            ok = super()._switch_bucket(bucketIndex, mode)
            if ok:
                # mode 0x4 displays the bucket; other modes (e.g. 0x2 =
                # liquid view) leave no bucket on screen
                self._active_bucket = bucketIndex if mode == 0x4 else None
            return ok

        def _delete_all_buckets(self):
            super()._delete_all_buckets()
            self._active_bucket = None

        def soft_clear_inactive(self) -> None:
            """Delete every bucket except the one currently displayed.

            Frees the image memory without switching the LCD to the liquid
            view, i.e. without any visible flash.
            """
            for index in range(_BUCKET_COUNT):
                if index != self._active_bucket:
                    self._delete_bucket(index)

        def _send_data(self, data, bulkInfo):
            # Reimplementation of KrakenZ3._send_data (liquidctl 1.15.0),
            # byte-identical protocol, but with verified bucket setup —
            # see the module docstring for the rationale.
            assert self.bulk_device, "Cannot find bulk out device"

            self._write_then_read([0x36, 0x03])

            buckets = self._query_buckets()
            bucketIndex = self._find_next_unoccupied_bucket(buckets)
            bucketIndex = self._prepare_bucket(
                bucketIndex if bucketIndex != -1 else 0, bucketIndex == -1)

            header = [0x12, 0xFA, 0x01, 0xE8, 0xAB, 0xCD,
                      0xEF, 0x98, 0x76, 0x54, 0x32, 0x10] + bulkInfo
            dataSize = math.ceil((len(header) + len(data)) / 1024)
            dataSizeBytes = list(dataSize.to_bytes(2, "little"))
            bucketMemoryStart = self._get_bucket_memory_offset(
                buckets, bucketIndex, dataSize)

            if bucketMemoryStart == -1:  # no gap found: reset and start over
                self._delete_all_buckets()
                bucketIndex = 0
                bucketMemoryStart = [0x0, 0x0]

            if not self._setup_bucket(bucketIndex, bucketIndex + 1,
                                      bucketMemoryStart, dataSizeBytes):
                # 2024 Elite firmware refuses sporadically even with free
                # memory; clear everything and retry once from offset 0
                log.info("bucket setup refused, clearing image memory and retrying")
                self._delete_all_buckets()
                bucketIndex = 0
                if not self._setup_bucket(0, 1, [0x0, 0x0], dataSizeBytes):
                    raise BucketSetupRefused(
                        "device refused the bucket setup twice, upload aborted")

            self._write_then_read([0x36, 0x01, bucketIndex])  # start transfer
            self._bulk_write(header)
            for i in range(0, len(data), self.bulk_buffer_size):
                self._bulk_write(list(data[i:i + self.bulk_buffer_size]))
            self._write([0x36, 0x02])  # end transfer

            if not self._switch_bucket(bucketIndex):
                raise BucketSetupRefused(
                    "device refused to switch to the newly written bucket")

    _patched_cls = PatchedKrakenZ3
    return _patched_cls


def find_patched_kraken():
    """Find the Kraken and upgrade its driver instance to the patched class.

    liquidctl's device registry only ever instantiates its own classes, so
    the stock instance is located first and then re-classed — a clean swap
    because ``PatchedKrakenZ3`` adds no constructor state. Returns None when
    no device is present. Raises RuntimeError when the installed liquidctl
    is incompatible with the patch (caller falls back to the stock driver).
    """
    cls = patched_driver_class()
    from liquidctl import find_liquidctl_devices
    for candidate in find_liquidctl_devices():
        if ("kraken" in candidate.description.lower()
                and hasattr(candidate, "set_screen")):
            if type(candidate) is not cls.__mro__[1]:  # not exactly KrakenZ3
                raise RuntimeError(
                    f"unexpected driver class {type(candidate).__name__}")
            candidate.__class__ = cls
            log.debug("driver instance upgraded to PatchedKrakenZ3")
            return candidate
    return None
