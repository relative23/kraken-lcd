# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [1.4.0] – 2026-09-25

The screens of the 2.0 design concept, on the 1.x daemon: twelve faces
next to the eight tiles, new backgrounds for all of them, and the roadmap
to 2.0 in `docs/ROADMAP.md`. Nothing changed in the device layer.

### Added

- **Twelve face screens** (`kraken_lcd/faces/`), each a full layout with
  many values and an animation that shows no invented data: light sweeps
  along gauges, a scan light over the core bars, sparks and a reading
  cursor along charts, a turning dial, a spinning record, a blinking clock
  colon. `cockpit`, `helm` (the cockpit's values around the rim of the
  round display, one bar per core, temperatures and loads colored by
  heat), `orbit`, `duo`, `history`, `fire` and `caustics` (the new
  backgrounds with secondary values), `video`, `clock`, `music`
  (an example track until a user-session helper can read the media
  player), `alarm` (appears only while the CPU or GPU is at
  `[screens.alarm] threshold`, default 90 °C) and `night` (only during
  `[screens.night] hours`, default 22 to 7). They go into
  `carousel.screens` like tiles and share the upload path, budget and
  cache: a face is re-rendered only when something it shows changed.
  Measured at 640 px with live values: 0.01 to 0.90 MB per upload.
  The cockpit layout is inspired by the dashboard that lukas-shawford
  runs on the same cooler, shown in his video in
  [liquidctl#774](https://github.com/liquidctl/liquidctl/issues/774#issuecomment-5707870902).
- `render.language` (`"en"` or `"de"`) for the words on the faces.
- Detailed sensor values for the faces: load per core, CPU clock, RAM and
  VRAM in GB, GPU power (nvidia-smi or amdgpu sysfs). Read only when a
  face is shown; tiles measure exactly as before.
- A 24-hour history of liquid temperature, CPU and GPU load and CPU
  temperature (one point per minute at most, `history.json` next to the
  render cache) for the charts.
- `assets/video.gif`, an evening landscape loop behind the video face,
  and the fonts the faces use (Barlow, Barlow Condensed, IBM Plex Mono;
  SIL Open Font License, texts in `assets/fonts/`).

### Changed

- **New default backgrounds**, one technique per scene, all laid out for
  the round LCD and looping seamlessly:
  - `liquid.gif`: pool-floor caustics. A looping wave spectrum refracts
    a dense grid of light rays; their density on the floor is the
    caustic brightness.
  - `cpu.gif`: a ring of fire. The value sits on the dark hearth
    (radius 0.36 of the width; the renderer draws "100%" out to 0.347, so
    the value never touches the flames). Flames are built like a fire
    shader and made to loop: periodic noise morphs around a circle and
    scrolls outward by whole periods, a second noise bends the tongues,
    a threshold growing with height tapers them; sparks rise and wink
    out before they wrap; embers glow on the hearth's rim.
  - `ram.gif`: a memory die on a dark board. Copper traces bend at 45
    degrees from the rim to pads on the die; data pulses run along them
    in and out, whole traces per loop, with a flash where they arrive.
    The RAM tile had shared `cpu.gif` before.
  - `gpu.gif`: a wireframe geodesic sphere spinning a fifth of a turn
    per loop, which brings it back onto itself.
  - `temp.gif`: a plasma globe. Its core sits between the two values.

  Ramp palettes are interpolated in OKLab, with an ordered dither on the
  pixels that stay still; the fire, the die and the lake get a palette
  fitted to their frames (k-means in OKLab) and an ordered dither that
  stays in place. Parts that do not move (disc, backdrop, the
  corners outside the round display) cost almost nothing in the GIF, so
  every tile is smaller than before. Measured with the renderer at 640
  px, the development host's per-tile settings and the same sensor
  values:

  | tile | before | after |
  |---|---|---|
  | liquid, pump, fan | 1.14 MB | 0.69–0.71 MB |
  | cpu | 1.39 MB | 1.12 MB |
  | ram | 1.39 MB | 0.74 MB |
  | gpu, nvme | 1.27–1.29 MB | 1.05–1.06 MB |
  | temps | 1.12 MB | 0.67 MB |
  | all eight | 9.89 MB | 6.72 MB |

  Rendered tiles keep the same GIF layout (GIF89a, one 64-color global
  palette, transparent delta frames, disposal 0). The difference: most
  delta frames now cover only the region that changed instead of the
  full 640 × 640.
- `scripts/generate_backgrounds.py` needs numpy (the daemon does not);
  each scene is its own module in `scripts/backgrounds/`, rendered either
  onto a color ramp or in true color with a fitted palette. It keeps 20 frames per GIF, so a tile with a
  lowered `max_frames` still gets the whole loop (the development host
  runs liquid, pump and fan at 20).

## [1.3.3] – 2026-09-16

The device's periodic status broadcast (`0x75 0x02`, about one per
second) was found in the way of two things, both verified with a passive
HID capture on the development machine.

### Fixed

- **The LCD reset at service stop failed with "missing messages"**
  whenever the stop came more than ~11 s after the last status read.
  liquidctl clears the OS report queue before a status read but not
  before `set_screen`, which then looks at 12 reports at most — the
  reply sat behind the stale broadcasts. The device layer now drains
  the queue before every command it issues (reset, brightness, upload,
  memory clear).
- **Bucket replies attributed to the wrong command.** The stock
  `_write_then_read` returns the next report, whatever it is; a broadcast
  arriving between two commands shifted every following reply by one.
  The result checks still passed (byte 14 is `0x1` in the broadcast as
  well), so the upload proceeded with a bucket table read one entry off.
  The patched driver now reads until the matching reply (request
  `(a, b)` → report `(a + 1, b)`) and raises `ReplyMissing` otherwise,
  which the device layer treats like any failed upload attempt. Whether
  this was behind the sporadic setup refusals is to be seen in the
  refusal rate.

## [1.3.2] – 2026-09-16

Documentation corrections after reading the NZXT CAM logs of the
development machine (Windows dual boot), plus one installer fix. No
change to the daemon.

### Fixed

- `docs/firmware-notes.md` claimed NZXT CAM can bring a wedged device out
  of its bootloader. It cannot: CAM re-flashes the firmware, reports
  "stuck in reprogrammer mode" and offers the update again, endlessly;
  only cutting standby power recovers the device. The notes and the
  README troubleshooting say so now.
- The firmware version of the development device: liquidctl (and
  `kraken-lcd doctor`) get `1.2.0` from the device's firmware-info
  report; NZXT CAM shows the same firmware as `1.2.12` (official updates
  1.2.1 → 1.2.8 on 2025-10-06 and 1.2.8 → 1.2.12 on 2026-03-27). The
  docs name both readings.
- The README troubleshooting said a bootloader wedge is "typically caused
  by the stock driver's upload behavior this project exists to avoid";
  the development device wedges under the patched driver as well. The
  wedge data is updated (eight wedges since 2026-07-28, the first 40 s
  run included) together with what the CAM logs add: no wedge in the 14
  months before the device was first driven from Linux.
- `install.sh --help` dropped the last line of the usage text.
- README: test count (226).

## [1.3.1] – 2026-09-14

Error handling at the boundary between liquidctl/hidapi and the daemon.
The 1.3.0 cooldown did not cover a device that is on the bus but cannot
be opened; that case still ended the process and left recovery to systemd.

### Fixed

- **A device that could not be opened ended the process.** hidapi and
  pyusb report a busy or inaccessible device with a plain `OSError` /
  `USBError`, which escaped the device layer's error handling: instead of
  the connect retry at startup or the cooldown while running, the daemon
  exited and systemd restarted it until the start limit was reached. The
  device layer now reports it as `DeviceUnavailable` (checking for a
  bootloader first) and releases the half-opened handles. `status`,
  `reset` and `doctor` print the error instead of a traceback.
- The hourly upload-health line counts an upload that ends in a device
  error (e.g. the reconnect between two attempts cannot reach the device)
  as failed; it used to be missing from the summary.
- The cooldown log line no longer claims "3 consecutive upload failures"
  for every round; it names what started it (upload failures, a device
  error, a failed reconnect, no answer after reconnect).
- `docs/firmware-notes.md` gave the development device's firmware as 2.x;
  it reports 1.2.0.

## [1.3.0] – 2026-09-11

Field data from the journal of the development machine (six bootloader
wedges since July under an 8-tile / 20 s carousel, and a five-day restart
storm in August against a device that had stopped answering) drove two
changes to how the daemon treats a failing device.

### Changed

- **Cooldown instead of exit.** After three consecutive upload failures
  the daemon no longer exits (which made systemd restart it every ~30 s —
  1931 restarts in five days in August, each one re-initializing and
  re-uploading against a hung device). It hands the LCD back to the
  firmware, disconnects, waits 5, 15 and then 60 minutes between probes
  while keeping the watchdog alive, and resumes when the device answers
  again. A device that vanishes mid-upload gets the same treatment. The
  bootloader and unsupported-firmware cases remain final (exit code 78).
- **Unchanged tiles are not re-uploaded.** A tile whose rendered file is
  already on the LCD (single-tile display with stable values) is skipped;
  the device layer tracks what is on screen and forgets it after a reset,
  reconnect, full memory clear or failed upload.
- `config.toml` and the README now state the upload load formula
  (3600 / display_seconds uploads per hour) with the observed wedge data,
  so the trade-off is visible when choosing `display_seconds`.

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
