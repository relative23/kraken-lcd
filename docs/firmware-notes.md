# Kraken LCD firmware notes

Observations from developing against an NZXT Kraken 2024 Elite RGB
(`1e71:3012`, firmware 1.2.0), collected here because none of this is
documented anywhere else. Reported upstream as liquidctl
[#774 (comment)](https://github.com/liquidctl/liquidctl/issues/774) and
[#907](https://github.com/liquidctl/liquidctl/issues/907).

## Image buckets

The LCD firmware stores images in 16 "buckets" inside a shared image
memory. An upload is: query all buckets → pick an unoccupied one → delete
it → set it up (index, memory offset, size) → bulk-stream the image →
switch the active bucket.

- **Setup refusals are sporadic and not purely memory-related.** The
  firmware refuses `setup` anywhere between ~5 and ~10 MB of accumulated
  uploads, non-deterministically — occasionally directly after a full
  clear. Treat a refused setup as a hard stop: clear the image memory,
  retry once from offset 0, and abort if refused again.
- **Never stream after a refused setup.** Stock liquidctl (every release
  up to 1.16.0 and `main` as of 2026-09) only logs the refusal and
  streams anyway; under a periodic upload workload this wedged our
  device into its bootloader twice. The proposed upstream fix is in
  [UPSTREAM.md](UPSTREAM.md).
- **Cleanup without flashing:** `_delete_all_buckets()` switches the LCD
  to the firmware liquid view first (visible flash). Deleting every bucket
  *except* the currently displayed one frees the memory with no visible
  interruption; track the active bucket via the switch command.
- The usable image memory appears smaller and less predictable than the
  24 320 KB constant in the driver suggests. kraken-lcd keeps a byte
  budget (default 6 MB) and clears proactively.

## Bootloader mode (`1e71:3011 "NZXT BOOTarea"`)

Under sustained upload stress (see above) the device can drop off the bus
and re-enumerate as its bootloader. In that state:

- The LCD is dead; **the pump keeps running on hardware** — cooling is
  not affected.
- Soft USB resets (usbreset, authorized toggle, unbind/bind) do **not**
  recover it, and neither does a plain reboot: the MCU stays up on
  standby power. Recovery requires cutting power completely for ~30 s
  (PSU switch / unplug). NZXT CAM on Windows can also do the official
  bootloader handshake.
- While a `3011` device is attached, liquidctl's device discovery crashes
  with `ValueError: The device has no langid` (it reads the string
  descriptors of every candidate USB device, and the bootloader has
  none) — this breaks discovery for *all* devices on the system
  (liquidctl [#907](https://github.com/liquidctl/liquidctl/issues/907)).
  kraken-lcd checks for `3011` before and after discovery and exits with
  code 78, which the systemd unit translates into "do not restart".

## Status readings

The first `get_status()` right after `initialize()` can return garbage
(e.g. a liquid temperature of 2 °C). kraken-lcd discards one read after
connecting and applies a plausibility window (5–90 °C) with a short
retry.
