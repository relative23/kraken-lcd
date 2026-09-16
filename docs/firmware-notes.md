# Kraken LCD firmware notes

Observations from developing against an NZXT Kraken 2024 Elite RGB
(`1e71:3012`), collected here because none of this is documented anywhere
else. Reported upstream as liquidctl
[#774 (comment)](https://github.com/liquidctl/liquidctl/issues/774) and
[#907](https://github.com/liquidctl/liquidctl/issues/907).

## Firmware version

liquidctl takes the version from the device's firmware-info report
(`0x10 0x01` → bytes `0x11`–`0x13`) and gets **1.2.0** for this device;
that is what `kraken-lcd doctor` and the connect log line show. NZXT CAM
reports the same firmware as **1.2.12** — its update history for this
device is 1.2.1 → 1.2.8 (2025-10-06) and 1.2.8 → 1.2.12 (2026-03-27);
the bootloader reports 0.2.0. CAM evidently reads the patch level through
another command. When the exact version matters, take it from CAM.

## Status broadcasts and the reply stream

The device sends an unsolicited status report (`0x75 0x02`, same layout
as the reply to the status request) about once per second, from the
moment `initialize()` has set the update interval. Two consequences,
both verified with a passive `hidraw` capture on 2026-09-16:

- **hidraw queues these reports** (64 deep, per open handle). liquidctl
  clears the queue before a status read but not before `set_screen`,
  which then looks at no more than 12 reports for its reply. So any
  `set_screen` issued more than ~11 s after the last status read fails
  with `missing messages (attempts=12, missing=1)` — the reply is there,
  behind the stale broadcasts. That is why the LCD reset at service stop
  failed whenever the stop came mid-wait. kraken-lcd drains the queue
  before every command it issues (1.3.3).
- **One report per command is not a protocol.** `_write_then_read`
  returns the next report, whatever it is. A broadcast that arrives
  between two commands is taken as the reply to the second one, and every
  reply after that is attributed to the previous command: the bucket
  table comes back shifted by one entry, the delete/setup/switch checks
  still pass (byte 14 is `0x1` in all of those replies as well as in the
  broadcast) and the upload's memory offset is computed from the wrong
  entries. Captured live: a broadcast landed between the LCD-info reply
  and the `0x36 0x03` request, the upload went through "successfully".
  The device answers request `(a, b)` with report `(a + 1, b)` for every
  command in the upload path (`0x30 0x04` → `0x31 0x04`, `0x32 0x01` →
  `0x33 0x01`, `0x36 0x03` → `0x37 0x03`, `0x38 0x01` → `0x39 0x01`, …),
  so the patched driver reads until the matching reply instead (1.3.3).

Whether the sporadic setup refusals below are this desync in disguise —
an offset computed from a shifted table that the firmware then rejects —
is the obvious hypothesis; the refusal rate after 1.3.3 will tell.

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
  [UPSTREAM.md](UPSTREAM.md). Note that the wedges did not stop once the
  blind stream was gone (see below): the abort makes a failed upload
  visible, it does not make the workload safe.
- **Cleanup without flashing:** `_delete_all_buckets()` switches the LCD
  to the firmware liquid view first (visible flash). Deleting every bucket
  *except* the currently displayed one frees the memory with no visible
  interruption; track the active bucket via the switch command.
- The usable image memory appears smaller and less predictable than the
  24 320 KB constant in the driver suggests. kraken-lcd keeps a byte
  budget (default 6 MB) and clears proactively.

## Bootloader mode (`1e71:3011 "NZXT BOOTarea"`)

Under sustained upload load the device can drop off the bus and
re-enumerate as its bootloader. In that state:

- The LCD is dead; **the pump keeps running on hardware** — cooling is
  not affected.
- Soft USB resets (usbreset, authorized toggle, unbind/bind) do **not**
  recover it, and neither does a plain reboot: the MCU stays up on
  standby power. Recovery requires cutting power completely for ~30 s
  (PSU switch / unplug).
- **NZXT CAM cannot recover it either.** On a dual-boot machine CAM finds
  the bootloader, locks every setting behind a firmware update, writes
  the firmware (131 KB, "100 %", "Firmware updated completed") and then
  fails with "The device is stuck in reprogrammer mode after finishing
  the firmware update and trying to reboot into application mode" — and
  offers the same update again, endlessly, without showing a version.
  Seen in CAM's `cam.log` on 2026-06-14 (three attempts) and 2026-09-15
  (two). The re-flash did no harm (the device came back on 1.2.12 after
  the power cut), but it is pointless: cut the power instead.
- While a `3011` device is attached, liquidctl's device discovery crashes
  with `ValueError: The device has no langid` (it reads the string
  descriptors of every candidate USB device, and the bootloader has
  none) — this breaks discovery for *all* devices on the system
  (liquidctl [#907](https://github.com/liquidctl/liquidctl/issues/907)).
  kraken-lcd checks for `3011` before and after discovery and exits with
  code 78, which the systemd unit translates into "do not restart".

### Wedge history, and what the CAM logs add

The development machine dual-boots Windows with NZXT CAM, whose
`cam.log` reaches back to 2025-04. In the 14 months before Linux first
drove the device (2026-06-04: liquidctl installed, first uploads) CAM
never saw it enumerate as its bootloader except during its two official
firmware updates; CAM's own log holds just two upload timeouts in that
time (2025-06-28, 2025-07-06), both recovered without a wedge. The first
wedge came on 2026-06-05. Since then the kernel log shows the bootloader
on 23 different days (development phase included), and the released
daemon (≥ 1.1.0) wedged the device eight times between 2026-07-28 and
2026-09-15:

- six runs at 180 uploads/h (20 s per tile): 2.9, 13.9, 11.5, 39.3, 2.5
  and 8.3 h before the wedge;
- the first run at 90 uploads/h (40 s): 16.5 h, ~1,450 uploads, none
  failed, refusal rate 2–18 % per hour without any ramp-up before the
  end;
- the run after the following power cycle: within two hours, at the
  hand-over to Windows — the LCD reset at Linux shutdown already failed
  ("missing messages"), CAM saw the device fail 10 s after taking it
  over and the bootloader 30 s later.

So the wedge correlates with the upload workload as such, not with a
specific command: it happens with the patched driver, which never
streams into a refused bucket, and no upload rate has been found yet at
which it does not happen eventually. The device dies while idle, 0–20 s
after a successful upload. What inside it gives way is unknown; CAM
writes the LCD far less often than a carousel does, which is the one
difference between the two workloads that is known for certain.

## Status readings

The first `get_status()` right after `initialize()` can return garbage
(e.g. a liquid temperature of 2 °C). kraken-lcd discards one read after
connecting and applies a plausibility window (5–90 °C) with a short
retry.
