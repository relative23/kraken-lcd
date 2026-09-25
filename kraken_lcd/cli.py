"""Command line interface: run / status / render / reset / doctor."""

import argparse
import logging
import os
import platform
import sys
from dataclasses import replace
from pathlib import Path

from . import __version__, upstream
from .cache import RenderCache
from .carousel import DEFAULT_RENDER_SIZE, Carousel
from .config import Config, ConfigError, load_config
from .device import (
    EXIT_NO_RESTART,
    DeviceError,
    DeviceInBootloader,
    DeviceStatus,
    DeviceUnsupported,
    KrakenDevice,
)
from .history import History
from .render import render_screen
from .screens import build_screens, effective_render_config
from .sensors import SensorReader

log = logging.getLogger("kraken_lcd")

BASE_DIR = Path(__file__).resolve().parent.parent
SYSTEM_CONFIG = Path("/etc/kraken-lcd/config.toml")


def _setup_logging(verbose: bool) -> None:
    fmt = "%(levelname)-7s %(name)s: %(message)s"
    if not os.environ.get("INVOCATION_ID"):  # journald adds its own timestamps
        fmt = "%(asctime)s " + fmt
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format=fmt)


def _load(args: argparse.Namespace) -> Config:
    """Config search order: --config > /etc/kraken-lcd/ > <project>/config.toml."""
    if args.config:
        path = Path(args.config).resolve()
    elif SYSTEM_CONFIG.is_file():
        path = SYSTEM_CONFIG
    else:
        path = None  # load_config falls back to BASE_DIR/config.toml, then defaults
    return load_config(path, BASE_DIR)


def cmd_run(args: argparse.Namespace) -> int:
    cfg = _load(args)
    cache = RenderCache(cfg.cache.dir, int(cfg.cache.max_megabytes * 2**20))
    carousel = Carousel(cfg, KrakenDevice(cfg.device), SensorReader(), cache)
    carousel.install_signal_handlers()
    try:
        carousel.run()
    except (DeviceInBootloader, DeviceUnsupported) as exc:
        # a restart cannot fix either; the unit does not retry exit code 78
        log.critical("%s", exc)
        return EXIT_NO_RESTART
    except DeviceError as exc:
        log.error("%s", exc)
        return 1
    except Exception:
        # last resort: a readable log line instead of a bare traceback
        # (systemd restarts us; a bootloader is then detected on reconnect)
        log.exception("unexpected error, shutting down")
        return 1
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    cfg = _load(args)
    device = KrakenDevice(cfg.device)
    description = "not reachable"
    status = DeviceStatus()
    try:
        device.connect()
        description = device.description
        status = device.read_status()
    except DeviceError as exc:
        log.warning("%s", exc)
    finally:
        device.disconnect()
    snap = SensorReader().snapshot(liquid_temp=status.liquid_temp,
                                   pump_rpm=status.pump_rpm,
                                   fan_rpm=status.fan_rpm)

    def fmt(value, unit: str) -> str:
        return f"{value:.1f} {unit}" if value is not None else "n/a"

    print(f"Device:      {description}")
    print(f"Liquid temp: {fmt(snap.liquid_temp, '°C')}")
    print(f"Pump speed:  {snap.pump_rpm if snap.pump_rpm is not None else 'n/a'} rpm")
    print(f"Fan speed:   {snap.fan_rpm if snap.fan_rpm is not None else 'n/a'} rpm")
    print(f"CPU load:    {fmt(snap.cpu_load, '%')}")
    print(f"CPU temp:    {fmt(snap.cpu_temp, '°C')}")
    print(f"GPU load:    {fmt(snap.gpu_load, '%')}")
    print(f"GPU temp:    {fmt(snap.gpu_temp, '°C')}")
    print(f"RAM usage:   {fmt(snap.ram_percent, '%')}")
    print(f"NVMe temp:   {fmt(snap.nvme_temp, '°C')}")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    """Render all tiles to a folder without touching the device."""
    cfg = _load(args)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    reader = SensorReader()
    snap = reader.snapshot(liquid_temp=args.liquid)
    detailed = replace(reader.snapshot(liquid_temp=args.liquid, detailed=True),
                       history=History(cfg.cache.dir / "history.json").series())
    screens = build_screens(cfg.screen_styles, cfg.render.language)
    base_render = cfg.render
    if base_render.size == 0:  # auto-detect needs a device; use the default
        base_render = replace(base_render, size=DEFAULT_RENDER_SIZE)
        print(f"render size: {DEFAULT_RENDER_SIZE} px (auto-detect needs a "
              f"connected device; set render.size to override)")
    rendered = 0
    for name in cfg.carousel.screens:
        screen = screens[name]
        elements = screen.build(detailed if screen.face is not None else snap, cfg.cache)
        if elements is None:
            if screen.face is not None:
                print(f"{name}: skipped (nothing to show now: no data yet, "
                      "or its condition does not apply)")
            else:
                print(f"{name}: skipped (required sensor unavailable"
                      + (", use --liquid for the liquid tile)" if name == "liquid" else ")"))
            continue
        out = out_dir / f"{name}.gif"
        budget = int(cfg.device.max_upload_megabytes * 1024 * 1024)
        background = cfg.assets_dir / screen.background if screen.background else None
        render_screen(screen, elements, background, out,
                      effective_render_config(screen, base_render), budget, cfg.assets_dir)
        print(f"{name}: {out} ({out.stat().st_size / 2**20:.2f} MB)")
        rendered += 1
    return 0 if rendered else 1


def cmd_reset(args: argparse.Namespace) -> int:
    cfg = _load(args)
    device = KrakenDevice(cfg.device)
    try:
        device.connect()
        device.reset_to_liquid()
    except DeviceInBootloader as exc:
        log.critical("%s", exc)
        return EXIT_NO_RESTART
    except DeviceError as exc:
        log.error("%s", exc)
        return 1
    finally:
        device.disconnect()
    print("LCD reset to the built-in liquid temperature screen.")
    return 0


# exit code of `doctor` when the driver patch cannot activate (the CI canary
# against liquidctl main relies on it)
EXIT_PATCH_INACTIVE = 3


def cmd_doctor(args: argparse.Namespace) -> int:
    """Compatibility and device report for bug reports and CI.

    Exit codes: 0 all good, 3 driver patch inactive, 1 device requested but
    not reachable, 78 device in its bootloader.
    """
    cfg = _load(args)
    compat = upstream.installed()
    try:
        import liquidctl
        where = Path(liquidctl.__file__).parent
    except Exception:  # noqa: BLE001 - report, do not crash
        where = "not importable"
    print(f"kraken-lcd:    {__version__} (Python {platform.python_version()}, "
          f"{platform.system()} {platform.release()})")
    print(f"liquidctl:     {compat.liquidctl_version} ({where})")
    if not cfg.device.driver_patch:
        print("driver patch:  disabled in the configuration (device.driver_patch = false)")
        code = 0
    elif compat.compatible:
        print(f"driver patch:  active ({compat.summary()})")
        code = 0
    else:
        print(f"driver patch:  INACTIVE, the stock driver is used "
              f"(liquidctl {compat.liquidctl_version}):")
        for problem in compat.problems:
            print(f"               - {problem}")
        print("               see docs/liquidctl-compatibility.md")
        code = EXIT_PATCH_INACTIVE
    if args.no_device:
        return code

    device = KrakenDevice(cfg.device)
    try:
        device.connect()
        resolution = device.lcd_resolution
        lcd = f"{resolution[0]}x{resolution[1]}" if resolution else "unknown resolution"
        print(f"device:        {device.description} (firmware "
              f"{device.firmware_version or 'unknown'}, LCD {lcd})")
        print(f"driver class:  {device.driver_class}")
        status = device.read_status()
        print(f"status:        liquid {_fmt(status.liquid_temp, '°C')}, "
              f"pump {_fmt(status.pump_rpm, 'rpm')}, fan {_fmt(status.fan_rpm, 'rpm')}")
    except DeviceInBootloader as exc:
        print(f"device:        {exc}")
        return EXIT_NO_RESTART
    except DeviceError as exc:
        print(f"device:        {exc}")
        return code or 1
    finally:
        device.disconnect()
    return code


def _fmt(value, unit: str) -> str:
    if value is None:
        return "n/a"
    return f"{value:.1f} {unit}" if isinstance(value, float) else f"{value} {unit}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kraken-lcd",
        description="Live system stats on NZXT Kraken LCD coolers")
    parser.add_argument("--config", help="path to config.toml")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="run the carousel daemon").set_defaults(func=cmd_run)
    sub.add_parser("status", help="print sensor and device status").set_defaults(func=cmd_status)
    p_render = sub.add_parser("render", help="render tiles to disk (device untouched)")
    p_render.add_argument("--out", default="preview",
                          help="output directory (default: ./preview)")
    p_render.add_argument("--liquid", type=float, default=None,
                          help="fake liquid temperature so the liquid tile renders")
    p_render.set_defaults(func=cmd_render)
    sub.add_parser("reset", help="hand the LCD back to the firmware liquid screen"
                   ).set_defaults(func=cmd_reset)
    p_doctor = sub.add_parser(
        "doctor", help="report liquidctl compatibility, driver patch status and "
                       "the connected device (attach to bug reports)")
    p_doctor.add_argument("--no-device", action="store_true",
                          help="skip the USB part (compatibility check only)")
    p_doctor.set_defaults(func=cmd_doctor)
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    try:
        return args.func(args)
    except ConfigError as exc:
        log.error("configuration error: %s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
