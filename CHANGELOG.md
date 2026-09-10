# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [1.2.0] – 2026-09-11

The dependency on liquidctl's private driver internals — the project's
most fragile part — is now an explicit, tested contract instead of a
one-string heuristic, and the tooling to keep it that way ships with the
project.

### Added

- `kraken_lcd.upstream`: the contract behind the driver patch. Every
  `KrakenZ3` internal the patch calls, overrides or reimplements is listed
  with its signature and a fingerprint of its logic (token stream without
  comments, docstring and formatting; stable across Python versions). The
  patch activates only when all of them match; otherwise it stands down
  with the exact reasons. `python -m kraken_lcd.upstream` prints the
  fingerprints of the installed liquidctl for auditing a new release.
- `kraken-lcd doctor`: compatibility and device report for bug reports
  and CI — liquidctl version, patch verdict with reasons, device,
  firmware version, LCD resolution, driver class, status (exit code 3
  when the patch cannot activate; `--no-device` for CI).
- Upload-health summary: one journal line per hour with uploads, firmware
  bucket refusals (including the ones the patched driver recovers
  invisibly) and failures; flushed on shutdown so the last stretch before
  a device wedge is captured. `journalctl -u kraken-lcd | grep 'upload health'`.
- Firmware version and driver class are logged at connect.
- `DeviceUnsupported` (exit code 78, no restart): a Kraken 2023 on
  firmware 2.x cannot show GIFs through liquidctl (liquidctl #631); this
  used to end in a retry/reconnect/restart loop.
- NZXT Kraken 2024 Plus (`1e71:3014`, liquidctl ≥ 1.16) in the udev rule
  and the device table.
- CI: matrix over liquidctl 1.14.0 / 1.15.0 / 1.16.0 on Python 3.11–3.14,
  a clean-fallback check on 1.13.0, a weekly canary against liquidctl
  `main`, and `ruff` linting.
- `docs/liquidctl-compatibility.md` (contract, compatibility matrix,
  release procedure, exit strategy) and `docs/UPSTREAM.md` with the
  proposed liquidctl fix as a ready-to-apply patch — it passes liquidctl's
  own Kraken tests.
- Issue templates for device test reports and bug reports.
- Distribution-neutral installation: the README lists the packages for
  Arch, Fedora, Debian/Ubuntu, Alpine, NixOS and Gentoo plus a virtualenv
  route for distributions without a liquidctl package; `install.sh`
  accepts `--python <interpreter>`, checks that the interpreter can
  import liquidctl, Pillow and psutil before installing anything, and
  finds `nologin` wherever the distribution keeps it.

### Fixed

- On liquidctl 1.13.0 (Ubuntu 24.04) the driver patch activated although
  that driver has neither `bulk_buffer_size` nor the same upload code;
  the first real upload would have failed with an `AttributeError`. The
  contract stands the patch down there.
- The "no device found" message named only the 2024 Elite's USB ID; it
  now lists every supported model with the liquidctl release it needs.
- The patch-fallback warning was repeated on every reconnect; it is
  logged once per process now.
- `docs/UPSTREAM.md`, referenced from the driver patch since 1.0.0, did
  not exist.

## [1.1.0] – 2026-07-20

Hardening release after a field incident (2026-07-19): a 2024 Elite RGB
crashed into its bootloader (USB `1e71:3011`) after ~2 h of normal carousel
operation — the firmware had refused roughly every 9th bucket setup before
dying. All existing safeguards worked (bootloader detection, exit code 78,
restart suppression); this release reduces the load that provoked the
firmware in the first place and stops feeding data to a device that has
already become unresponsive.

### Added

- Transport health circuit breaker: after one full `read_status()` worth of
  consecutive transport errors, the daemon reconnects (which also detects a
  bootloader) *before* the next upload instead of streaming megabytes into
  an unresponsive device.

### Changed

- Default `carousel.display_seconds` raised from 10 to 20, halving the
  upload cadence and the image-memory churn that stresses the 2024 Elite
  firmware.
- `soft_clear_inactive()` queries the bucket table first and deletes only
  occupied buckets — routine memory housekeeping no longer sends
  state-mutating delete commands for buckets that are already empty.

## [1.0.0] – 2026-07-06

First public release.

### Added

- Rotating carousel of live system tiles on NZXT Kraken LCDs: liquid
  temperature, CPU load, GPU load, CPU/GPU temperatures, RAM usage, NVMe
  temperature, pump rpm and fan rpm — each burned into an animated GIF
  background.
- LCD resolution auto-detection for the whole Kraken Z / 2023 / 2023 Elite /
  2024 Elite family (240, 320 and 640 px panels).
- Patched KrakenZ3 upload path: the bucket setup is verified *before*
  streaming image data, sporadic firmware refusals are absorbed with an
  invisible retry, and routine image-memory cleanup no longer flashes the
  screen (stock liquidctl streams multi-megabyte uploads into refused
  buckets, which can wedge the device into its bootloader — see
  liquidctl issues [#774](https://github.com/liquidctl/liquidctl/issues/774)
  and [#907](https://github.com/liquidctl/liquidctl/issues/907)). The patch
  disables itself automatically on liquidctl versions it does not recognize.
- Proactive device image-memory accounting, upload size guard with adaptive
  frame thinning, and upload pacing.
- Optional pump/fan control: fixed duty or a (liquid °C → duty %) curve
  stored in the device firmware.
- Per-tile styling: label, background, text color, vertical positions,
  palette size and frame cap.
- Render cache keyed on everything that affects pixels, including the
  background file's identity — swapping a GIF takes effect without manual
  cache clearing.
- GPU stats from NVIDIA (`nvidia-smi`) or AMD (`amdgpu` hwmon/sysfs).
- systemd service with watchdog (`Type=notify`), dedicated unprivileged
  system user + udev rule, suspend/resume hook, bootloader detection with
  restart suppression (exit code 78), and an idempotent installer.
- 156 hardware-independent tests; CI via GitHub Actions.
