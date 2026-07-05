#!/usr/bin/env bash
# kraken-lcd installer (idempotent — safe to re-run for upgrades).
#
#   sudo ./install.sh              install or upgrade
#   sudo ./install.sh --uninstall  remove the service (keeps /etc config)
#
# Installs the application to /opt/kraken-lcd, the default configuration
# to /etc/kraken-lcd/config.toml (only when absent), a dedicated system
# user, the udev rule for device access, and the systemd units.
set -euo pipefail
cd "$(dirname "$0")"

APP_DIR=/opt/kraken-lcd
CONFIG_DIR=/etc/kraken-lcd

if [[ $EUID -ne 0 ]]; then
  echo "Please run with sudo: sudo $0 ${1:-}" >&2
  exit 1
fi

if [[ "${1:-}" == "--uninstall" ]]; then
  systemctl disable --now kraken-lcd.service kraken-lcd-sleep.service 2>/dev/null || true
  rm -f /etc/systemd/system/kraken-lcd.service \
        /etc/systemd/system/kraken-lcd-sleep.service \
        /etc/udev/rules.d/60-kraken-lcd.rules
  systemctl daemon-reload
  udevadm control --reload-rules
  rm -rf "$APP_DIR" /var/cache/kraken-lcd
  echo "kraken-lcd removed. Kept: $CONFIG_DIR (your configuration) and the"
  echo "kraken-lcd system user (remove with: userdel kraken-lcd)."
  exit 0
fi

# dedicated system user (no login shell, no home)
if ! getent group kraken-lcd >/dev/null; then
  groupadd --system kraken-lcd
fi
if ! getent passwd kraken-lcd >/dev/null; then
  useradd --system --gid kraken-lcd --no-create-home \
          --home-dir /nonexistent --shell /usr/sbin/nologin kraken-lcd
fi

# application to /opt (world-readable, root-owned)
mkdir -p "$APP_DIR"
rm -rf "$APP_DIR/kraken_lcd" "$APP_DIR/assets"
cp -r kraken_lcd assets "$APP_DIR/"
find "$APP_DIR" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
chmod -R a+rX "$APP_DIR"

# default configuration (never overwrite an existing one)
mkdir -p "$CONFIG_DIR"
if [[ ! -f "$CONFIG_DIR/config.toml" ]]; then
  install -m 644 config.toml "$CONFIG_DIR/config.toml"
  echo "Installed default configuration to $CONFIG_DIR/config.toml"
else
  echo "Keeping existing $CONFIG_DIR/config.toml"
fi

# udev rule: device access (hidraw + usb bulk) for the kraken-lcd group
install -m 644 systemd/60-kraken-lcd.rules /etc/udev/rules.d/60-kraken-lcd.rules
udevadm control --reload-rules
udevadm trigger --subsystem-match=usb --attr-match=idVendor=1e71 2>/dev/null || true
udevadm trigger --subsystem-match=hidraw 2>/dev/null || true

# cache ownership (CacheDirectory= chowns on start; fix any leftovers)
if [[ -d /var/cache/kraken-lcd ]]; then
  chown -R kraken-lcd:kraken-lcd /var/cache/kraken-lcd
fi

install -m 644 systemd/kraken-lcd.service /etc/systemd/system/kraken-lcd.service
install -m 644 systemd/kraken-lcd-sleep.service /etc/systemd/system/kraken-lcd-sleep.service
systemctl daemon-reload
systemctl enable kraken-lcd.service kraken-lcd-sleep.service
systemctl restart kraken-lcd.service
systemctl --no-pager --lines 5 status kraken-lcd.service
