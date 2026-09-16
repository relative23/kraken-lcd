"""A patched KrakenZ3 driver with 2024-Elite-safe bucket handling.

Stock liquidctl (verified in 1.14.0 through 1.16.0 and in upstream main as of 2026-09)
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

3. ``_write_then_read`` returns whatever report comes next. The device
   broadcasts a status report (``0x75 0x02``) about once per second; when
   one arrives between two commands, the driver takes it as the reply and
   every later reply is attributed to the previous command: the bucket
   table is read shifted by one entry, the result checks pass anyway
   (byte 14 is 0x1 in all of these replies) and the memory offset for
   the upload is computed from the wrong entries. Observed live with a
   passive HID capture on 2026-09-16.
   → ``_write_then_read`` reads until the *matching* reply (the device
   answers request ``(a, b)`` with report ``(a + 1, b)``) and raises
   ``ReplyMissing`` if it never comes.

The patch is deliberately defensive. It touches private liquidctl API,
which may change in any release, so it only activates when every internal
it depends on is exactly the code it was written against — checked by
signature and by a fingerprint of the method body, see
``kraken_lcd.upstream``. On any mismatch (a future liquidctl release that
fixes or reworks ``_send_data``, a version too old to have the 2023/2024
models, a build without source) ``patched_driver_class()`` raises and the
caller falls back to the stock driver. ``kraken-lcd doctor`` shows the
verdict and its reasons.

The upstream fix that would make this patch unnecessary is submitted as
liquidctl #927, see ``docs/UPSTREAM.md``.
"""

import logging
import math

from . import upstream

log = logging.getLogger(__name__)


class BucketSetupRefused(Exception):
    """The firmware refused the bucket setup / switch; the upload was
    aborted *before* any image data was streamed."""


class ReplyMissing(Exception):
    """The device did not answer a command within the read budget."""


# reports to look at for one reply: the OS queue holds 64, and between a
# drain and the command only a handful of broadcasts can arrive
MAX_REPLY_READS = 16

_patched_cls = None


def patched_driver_class():
    """Build (once) and return the patched KrakenZ3 subclass.

    Raises RuntimeError (with the reasons) when the installed liquidctl
    does not satisfy the upstream contract — callers must then use the
    stock driver.
    """
    global _patched_cls
    if _patched_cls is not None:
        return _patched_cls

    compatibility = upstream.installed()
    if not compatibility.compatible:
        raise RuntimeError(compatibility.summary())
    log.info("driver patch active: %s", compatibility.summary())

    from liquidctl.driver.kraken3 import KrakenZ3

    class PatchedKrakenZ3(KrakenZ3):
        """KrakenZ3 with verified bucket setup and flash-free cleanup."""

        _active_bucket: int | None = None
        # set by _send_data; read by the device layer's upload monitor to
        # track how often the firmware balks (recovered refusals are
        # otherwise invisible above this class)
        last_upload_refused: bool = False

        def _write_then_read(self, data):
            # the reply to request (a, b) is report (a + 1, b) for every
            # command this driver sends (0x30/0x32/0x36/0x38/0x74 families);
            # anything else in the queue is a periodic status broadcast or a
            # reply nobody waited for (e.g. to the write-only end-of-transfer
            # report), and taking it as *this* reply desyncs the whole
            # conversation — see the module docstring, item 3
            self._write(data)
            expected = bytes([data[0] + 1, data[1]])
            for _ in range(MAX_REPLY_READS):
                msg = self._read()
                if bytes(msg[0:2]) == expected:
                    return msg
                log.debug("skipped report %02x%02x while waiting for %02x%02x",
                          msg[0], msg[1], expected[0], expected[1])
            raise ReplyMissing(
                f"no reply to {data[0]:#04x} {data[1]:#04x} within "
                f"{MAX_REPLY_READS} reports")

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
            """Delete every occupied bucket except the one displayed.

            Frees the image memory without switching the LCD to the liquid
            view, i.e. without any visible flash. The bucket table is
            queried first (read-only) so that only occupied buckets get a
            state-mutating delete command — the firmware's delete handler
            is exactly where the sporadic refusals live, so it is not
            poked for buckets that are already empty.
            """
            buckets = self._query_buckets()
            for index, info in buckets.items():
                # occupancy convention as in _find_next_unoccupied_bucket:
                # bytes 15+ are all zero for an unoccupied bucket
                if index != self._active_bucket and any(info[15:]):
                    self._delete_bucket(index)

        def _send_data(self, data, bulkInfo):
            # Reimplementation of KrakenZ3._send_data (liquidctl 1.14.0-1.16.0, fingerprinted in kraken_lcd.upstream),
            # byte-identical protocol, but with verified bucket setup —
            # see the module docstring for the rationale.
            assert self.bulk_device, "Cannot find bulk out device"
            self.last_upload_refused = False

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
                self.last_upload_refused = True
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
    from liquidctl.driver.kraken3 import KrakenZ3
    for candidate in find_liquidctl_devices():
        if ("kraken" in candidate.description.lower()
                and hasattr(candidate, "set_screen")):
            if type(candidate) is not KrakenZ3:  # a subclass is not verified
                raise RuntimeError(
                    f"unexpected driver class {type(candidate).__name__}")
            candidate.__class__ = cls
            log.debug("driver instance upgraded to PatchedKrakenZ3")
            return candidate
    return None
