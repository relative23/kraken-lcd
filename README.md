# kraken-lcd

[![CI](https://github.com/relative23/kraken-lcd/actions/workflows/ci.yml/badge.svg)](https://github.com/relative23/kraken-lcd/actions/workflows/ci.yml)

Live system stats on the LCD of NZXT Kraken liquid coolers — as a rotating
carousel of tiles with animated backgrounds, or as a single always-on
display. Linux only (any distribution), no NZXT CAM, no Electron: one
small Python daemon on top of [liquidctl](https://github.com/liquidctl/liquidctl).

![Example tile](docs/tile-example.png)

## Features

- **Eight tiles**, individually selectable and ordered: liquid temperature,
  CPU load, GPU load, CPU+GPU temperatures, RAM usage, NVMe temperature,
  pump rpm, fan rpm. One tile in the list = a static live display.
- **Animated GIF backgrounds** with the live value burned in — bring your
  own GIFs or use the generated defaults.
- **Safe uploads.** The stock liquidctl driver streams multi-megabyte
  uploads into buckets the firmware has refused, which can wedge the
  device into its bootloader (see liquidctl
  [#774](https://github.com/liquidctl/liquidctl/issues/774) /
  [#907](https://github.com/liquidctl/liquidctl/issues/907) — reported
  from this project). kraken-lcd ships a patched upload path that verifies
  the bucket setup first, absorbs sporadic firmware refusals invisibly,
  manages the device's image memory proactively, enforces a size budget
  with adaptive frame thinning, and paces uploads. When the device stops
  answering status reads, uploads are held back until a reconnect
  succeeds — a sick device is never fed more data.
- **Contained dependency on liquidctl internals.** The patched upload
  path only activates when every liquidctl internal it relies on is
  verifiably the code it was written for (signature + fingerprint of each
  method); otherwise it stands down to the stock driver and says why.
  CI tests every supported liquidctl release and a weekly canary against
  liquidctl `main`. Details: [docs/liquidctl-compatibility.md](docs/liquidctl-compatibility.md).
- **Pump/fan curves** (optional): a `[liquid °C → duty %]` curve written to
  the device firmware — it keeps regulating even with the daemon stopped.
- **Robust as a service:** systemd watchdog, dedicated unprivileged user,
  suspend/resume hook, bootloader detection with clear recovery
  instructions and restart suppression. A device that stops answering is
  left alone: the daemon hands the LCD back to the firmware and cools down
  (5, 15, then 60 min between probes) instead of restarting against it.
- **Per-tile styling:** labels, colors, positions, palette/frame tuning.

## Supported devices

| Device | USB ID | LCD | needs liquidctl |
|---|---|---|---|
| NZXT Kraken Z53 / Z63 / Z73 | `1e71:3008` | 320 × 320 | ≥ 1.13 |
| NZXT Kraken 2023 | `1e71:300e` | 240 × 240 | ≥ 1.14 (see note) |
| NZXT Kraken 2023 Elite | `1e71:300c` | 640 × 640 | ≥ 1.14 |
| NZXT Kraken 2024 Elite RGB | `1e71:3012` | 640 × 640 | ≥ 1.15 |
| NZXT Kraken 2024 Plus | `1e71:3014` | 240 × 240 | ≥ 1.16 |

The LCD resolution is auto-detected. Developed and continuously tested on a
2024 Elite RGB; the other models are driven by the same liquidctl driver
class. Test reports are very welcome — open a
[device report](https://github.com/relative23/kraken-lcd/issues/new?template=device-report.md)
with the output of `kraken-lcd doctor`.

Note: liquidctl cannot upload GIFs to a Kraken 2023 running firmware 2.x
(liquidctl [#631](https://github.com/liquidctl/liquidctl/issues/631)).
kraken-lcd detects this, logs it and exits without restart loops.

## Requirements

Linux with systemd (for the service; the daemon itself only needs Python),
Python ≥ 3.11, and the Python packages `liquidctl` (≥ 1.14 for the safe
upload path, see below), `Pillow` and `psutil`. Most distributions
package all three, which is the recommended route: no virtualenv, and a
Python upgrade cannot break the installation.

| Distribution | Install | liquidctl | driver patch |
|---|---|---|---|
| Arch, Manjaro | `pacman -S liquidctl python-pillow python-psutil` | 1.16.0 | active |
| Fedora 42+ | `dnf install liquidctl python3-pillow python3-psutil` | 1.16.0 | active |
| Fedora 40 / 41 | same | 1.15.0 | active |
| Debian 13, Ubuntu 25.10 / 26.04 | `apt install liquidctl python3-pil python3-psutil` | 1.15.0 | active |
| Debian testing | same | 1.16.0 | active |
| Ubuntu 25.04 | same | 1.14.0 | active |
| Ubuntu 24.04 | same | 1.13.0 | stands down: stock driver, Kraken Z only (see pip below) |
| Alpine 3.24 / edge | `apk add liquidctl py3-pillow py3-psutil` | 1.16.0 | active (no systemd: run the daemon under OpenRC yourself) |
| NixOS 26.05 / unstable | `liquidctl`, `python3Packages.pillow`, `python3Packages.psutil` | 1.16.0 | active |
| Gentoo | `liquidctl`, `dev-python/pillow`, `dev-python/psutil` | 1.16.0 | active |
| openSUSE, anything without a liquidctl package | pip, see below | 1.16.0 | active |

Package versions as of 2026-09. `kraken-lcd doctor` shows which case
applies to your machine.

**Without a distribution package** (or to get a newer liquidctl than the
distribution ships, e.g. on Ubuntu 24.04): install the dependencies into
a virtualenv and point the installer at its interpreter. liquidctl needs
the system's `libusb` and `hidapi` libraries.

```bash
sudo python3 -m venv /opt/kraken-lcd-venv
sudo /opt/kraken-lcd-venv/bin/pip install "liquidctl>=1.14" pillow psutil
sudo ./install.sh --python /opt/kraken-lcd-venv/bin/python3
```

GPU tiles use `nvidia-smi` (NVIDIA) or the `amdgpu` driver (AMD)
automatically. NZXT CAM or CoolerControl must not manage the same device
at the same time.

## Installation

```bash
git clone https://github.com/relative23/kraken-lcd.git
cd kraken-lcd
sudo ./install.sh
```

The installer is idempotent (re-run it to upgrade) and sets up: the
application in `/opt/kraken-lcd`, your configuration in
`/etc/kraken-lcd/config.toml` (never overwritten), a dedicated
`kraken-lcd` system user with a udev rule for device access, the systemd
service with watchdog, and a suspend/resume hook. It checks first that
the Python interpreter (default `/usr/bin/python3`, override with
`--python`) can import liquidctl, Pillow and psutil, and names what is
missing otherwise. Without systemd, skip the installer: install the
package and run `python3 -m kraken_lcd run` under your init system with
the udev rule from `systemd/60-kraken-lcd.rules`.

```bash
journalctl -u kraken-lcd -f          # watch it run
sudo systemctl stop kraken-lcd       # stop (hands the LCD back to firmware)
sudo ./install.sh --uninstall        # remove (keeps your /etc config)
```

Running from the checkout without installing works too:

```bash
python3 -m kraken_lcd doctor         # liquidctl compatibility, patch status, device
python3 -m kraken_lcd status         # sensors + device
python3 -m kraken_lcd render --liquid 40   # render tiles to ./preview, no device
python3 -m kraken_lcd run            # foreground carousel (Ctrl-C resets the LCD)
python3 -m kraken_lcd reset          # back to the firmware liquid screen
```

## Configuration

Everything lives in one commented file: `/etc/kraken-lcd/config.toml`
(falls back to the checkout's `config.toml`; `--config` overrides).
Restart the service after changes.

- **Tiles:** `carousel.screens` selects and orders the display.
- **Backgrounds:** drop GIFs into the assets directory (`liquid.gif`,
  `cpu.gif`, `gpu.gif`, `temp.gif` by default, or point any tile at any
  file via `[screens.<name>] background = "..."`). Changes are picked up
  automatically — the cache tracks file identity. The shipped backgrounds
  are generated by `scripts/generate_backgrounds.py`; tweak its color
  schemes and rebuild your own.
- **Styling:** `[screens.<name>]` overrides label, `color = "#RRGGBB"`,
  vertical positions and per-tile `colors`/`max_frames` (file-size tuning
  for noisy backgrounds).
- **Cooling:** `[cooling]` sets a fixed duty or a curve for `pump` and
  `fan`. Note: this replaces the firmware's default curve persistently
  (NZXT CAM can restore it).

## Troubleshooting

**Black display / service failed →** `journalctl -u kraken-lcd -n 50`.

**"Kraken is stuck in bootloader mode (USB 1e71:3011)":** the device
wedged (typically caused by the stock driver's upload behavior this
project exists to avoid). Recovery: shut down, **cut standby power for
~30 s** (PSU switch off or unplug — a reboot is not enough), boot. The
service intentionally stays stopped while a bootloader is detected
(exit code 78) and starts normally on the next boot.

**"cooling down for N min" in the log:** three uploads in a row failed
(typically every command times out while the device is still listed as
`1e71:3012`). The daemon disconnects and probes again after 5, 15 and then
every 60 minutes; the LCD shows the firmware screen meanwhile. If it does
not come back after an hour, the device needs the same power cut as the
bootloader case. Without this cooldown the daemon used to exit and be
restarted by systemd every ~3.6 minutes, for days.

**How much upload load is safe?** Every tile shown is one upload:
3600 / `display_seconds` per hour, independent of the tile count. On the
development 2024 Elite RGB, 180 uploads/h (20 s) ended in a bootloader
wedge after 2.5–39 h, six times in six weeks; 10 s wedged it within 2 h.
40 s halves the load. `journalctl -u kraken-lcd | grep 'upload health'`
shows uploads and firmware refusals per hour. A single tile whose values
do not change is not re-uploaded at all.

**No device found:** `lsusb | grep 1e71` must show one of the supported
IDs. Stop NZXT CAM / CoolerControl (`Conflicts=coolercontrold.service` is
declared by the unit).

**"driver patch disabled itself" in the log:** the installed liquidctl
does not match the code the patch was verified against — too old
(1.13.0), or a new release that changed the driver. Everything keeps
working on the stock driver, with occasional screen flashes during memory
cleanup and without the pre-stream verification. `kraken-lcd doctor`
lists the exact reasons; [docs/liquidctl-compatibility.md](docs/liquidctl-compatibility.md)
explains what to do about them. If a liquidctl release fixes #774
upstream, this is expected and fine.

**"cannot show GIFs through liquidctl" (exit code 78):** a Kraken 2023 on
firmware 2.x. liquidctl only supports static images there (#631); the
service stays stopped because a restart cannot help.

**Health over time:** `journalctl -u kraken-lcd | grep 'upload health'`
prints one line per hour with uploads, firmware bucket refusals and
failures — the number to watch after a config or liquidctl change.

**GPU tile missing:** needs a working `nvidia-smi` or the `amdgpu`
kernel driver; without either the tile is skipped silently.

## Development

```bash
python3 -m pytest      # 217 tests, no hardware required
ruff check .           # lint (same as CI)
python3 -m kraken_lcd.upstream   # fingerprints of the installed liquidctl internals
```

CI runs the suite on Python 3.11–3.14 against liquidctl 1.14.0, 1.15.0
and 1.16.0, checks that 1.13.0 degrades cleanly, and runs a weekly canary
against liquidctl `main`.

- [docs/liquidctl-compatibility.md](docs/liquidctl-compatibility.md) —
  how the driver patch is guarded, the compatibility matrix, and the
  procedure for a new liquidctl release.
- [docs/UPSTREAM.md](docs/UPSTREAM.md) — the proposed upstream fix that
  makes the patch unnecessary.
- [docs/firmware-notes.md](docs/firmware-notes.md) — firmware behavior
  (bucket protocol, bootloader wedge).

## License

GPL-3.0-or-later — see [LICENSE](LICENSE). The patched upload path in
`kraken_lcd/driver_patch.py` is derived from liquidctl's GPLv3 KrakenZ3
driver.

This project is not affiliated with or endorsed by NZXT. "Kraken" is a
product name of NZXT, used here to describe compatibility.
