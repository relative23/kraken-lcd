# Upstream contribution to liquidctl

kraken-lcd's driver patch exists because of one line in liquidctl's
`KrakenZ3._send_data`: a refused bucket setup is logged and the image is
streamed anyway. Fixing that upstream makes the most fragile part of this
project unnecessary (see [liquidctl-compatibility.md](liquidctl-compatibility.md),
"Exit strategy").

## Status

- liquidctl [#774](https://github.com/liquidctl/liquidctl/issues/774):
  firmware refusals and the bootloader wedge, reported from this project.
- liquidctl [#907](https://github.com/liquidctl/liquidctl/issues/907):
  device discovery crashes while a bootloader device is on the bus.
- The bug is present in 1.13.0 through 1.16.0 and in `main` as of
  2026-09-10 (commit `292dc561`).

## The patch

[`upstream/0001-kraken3-abort-the-upload-when-the-device-refuses-the-bucket-setup.patch`](upstream/0001-kraken3-abort-the-upload-when-the-device-refuses-the-bucket-setup.patch),
made against liquidctl `main` (`292dc561`, 2026-06-16). It changes one
thing: a refused `_setup_bucket` raises `ExpectationNotMet` before any
bulk data is written. The bucket-switch failure after a successful
stream keeps its log line, since nothing more is sent at that point and
changing it would break liquidctl's existing mock test.

It adds one regression test (`test_krakenz3_refused_bucket_setup_aborts_before_streaming`)
and a changelog entry. liquidctl's `tests/test_kraken3.py` passes with
it applied (31 passed, 1 pre-existing skip).

Apply with:

```bash
git clone https://github.com/liquidctl/liquidctl.git && cd liquidctl
git am /path/to/kraken-lcd/docs/upstream/0001-*.patch
python -m pytest tests/test_kraken3.py
```

## Pull request text (draft)

> **kraken3: abort the upload when the device refuses the bucket setup**
>
> `KrakenZ3._send_data` only logs a refused bucket setup and then streams
> the whole image anyway. The data cannot land anywhere, the bucket switch
> fails afterwards, and the caller never learns the image did not reach
> the screen.
>
> On the Kraken 2024 Elite the firmware refuses the setup sporadically
> even with free image memory. Streaming regardless, under a periodic
> upload workload, has wedged two of these devices into the bootloader
> (`1e71:3011`), recoverable only by cutting standby power (#774).
>
> This raises `ExpectationNotMet` before any bulk data is sent. Tested on
> a 2024 Elite RGB (firmware 1.2.0): with the abort in place the daemon
> in relative23/kraken-lcd has run for weeks without a wedge, with the
> refusals showing up as handled exceptions. `tests/test_kraken3.py`
> passes; one regression test added.
>
> Not changed: the "Failed to switch active bucket" log after a
> successful stream. Happy to turn that into an exception too if you
> prefer, it needs a small change to `MockKraken`'s switch reply.

## After it is merged

Nothing to do in kraken-lcd for the fix to take effect: the `_send_data`
fingerprint in `kraken_lcd/upstream.py` no longer matches, the patch
stands down, and the stock driver's exception is handled by the device
layer's normal retry path. Follow-up for a later release: drop the patch
for liquidctl versions that contain the fix, keep the flash-free soft
clear if it can be expressed on the public API by then.
