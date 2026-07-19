# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

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
