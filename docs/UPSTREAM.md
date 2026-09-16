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
- **Submitted as liquidctl [#927](https://github.com/liquidctl/liquidctl/pull/927)
  on 2026-09-11** (branch `kraken3-abort-on-refused-bucket-setup` on
  `relative23/liquidctl`).
- Field data after the PR: with the abort in place the development
  device still wedges under a sustained upload workload (eight times
  between 2026-07-28 and 2026-09-15 at 90–180 uploads/h, see
  [firmware-notes.md](firmware-notes.md)). The abort removes the blind
  stream into a refused bucket and makes the failure visible to the
  caller; it does not prevent the wedge. The PR text gives the device's
  firmware as 1.2.0, which is liquidctl's reading; NZXT CAM shows 1.2.12.
  Both points were reported in comments on #774 and #927 on 2026-09-16.

## The patch

[`upstream/0001-kraken3-abort-the-upload-when-the-device-refuses-the-bucket-setup.patch`](upstream/0001-kraken3-abort-the-upload-when-the-device-refuses-the-bucket-setup.patch),
made against liquidctl `main` (`292dc561`, 2026-06-16). It changes one
thing: a refused `_setup_bucket` raises `ExpectationNotMet` before any
bulk data is written. The bucket-switch failure after a successful
stream keeps its log line, since nothing more is sent at that point and
changing it would break liquidctl's existing mock test.

It adds one regression test (`test_krakenz3_refused_bucket_setup_aborts_before_streaming`).
No changelog entry: liquidctl's process document reserves the CHANGELOG
for the maintainers. liquidctl's `tests/test_kraken3.py` passes with it
applied (31 passed, 1 pre-existing skip), and `black --check` is clean.

Apply with:

```bash
git clone https://github.com/liquidctl/liquidctl.git && cd liquidctl
git am /path/to/kraken-lcd/docs/upstream/0001-*.patch
python -m pytest tests/test_kraken3.py
```

## Pull request

Open as [liquidctl#927](https://github.com/liquidctl/liquidctl/pull/927).
The description states what changed, what was tested (liquidctl's Kraken
tests, and the same abort in kraken-lcd's subclass on a 2024 Elite RGB
since July) and what is not addressed (the sporadic refusals themselves).

## After it is merged

Nothing to do in kraken-lcd for the fix to take effect: the `_send_data`
fingerprint in `kraken_lcd/upstream.py` no longer matches, the patch
stands down, and the stock driver's exception is handled by the device
layer's normal retry path. Follow-up for a later release: drop the patch
for liquidctl versions that contain the fix, keep the flash-free soft
clear if it can be expressed on the public API by then.
