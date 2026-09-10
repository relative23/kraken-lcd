# liquidctl compatibility and the driver patch

kraken-lcd's safe upload path (`kraken_lcd/driver_patch.py`) is a subclass
of liquidctl's `KrakenZ3` driver that replaces one private method
(`_send_data`) and calls eleven others. Private API can change in any
liquidctl release. This document describes how that risk is contained,
what the current state is, and what to do when a liquidctl release
changes something.

## The contract

`kraken_lcd/upstream.py` lists every `KrakenZ3` internal the patch depends
on, with its call signature and a *fingerprint* of its body, plus the
constructor parameters the device layer needs (`bulk_buffer_size`,
`lcd_resolution`, both since liquidctl 1.14). Before the patch is allowed
to activate, the installed liquidctl is checked against that list:

| Check | Fails when |
|---|---|
| method exists | upstream removed or renamed it |
| signature matches | upstream added, removed or renamed a parameter |
| fingerprint matches | upstream changed the *logic*: any added, removed or changed token |
| source available | liquidctl is installed without `.py` sources |

The fingerprint hashes the token stream of the method with comments,
docstring, blank lines and the function name removed and with indentation
kept as structure. So a docstring fix, a comment, or re-wrapping long
lines inside brackets upstream do **not** disable the patch; any change to
the code does, including a renamed local variable, an added trailing comma
or an extra pair of parentheses. That is deliberately conservative: a
false "changed" only costs the patch (the daemon keeps running on the
stock driver), a false "unchanged" could feed a multi-megabyte stream into
a device whose driver now behaves differently.

The fingerprint is based on tokens rather than the AST so that it is
stable across Python versions (`ast.dump` output changed in 3.13, for
example). A Python upgrade must not silently switch the daemon to the
unsafe path. This is covered by CI on Python 3.11 through 3.14 (3.14 is what Ubuntu 26.04 ships).

When the check fails, `patched_driver_class()` raises `RuntimeError` with
every reason, the device layer logs one warning and uses the stock driver,
and `kraken-lcd doctor` prints the reasons and exits with status 3.

## Current state (2026-09)

| liquidctl | KrakenZ3 internals | driver patch | devices known to liquidctl |
|---|---|---|---|
| 1.13.0 | different `_send_data`, `_bulk_write`, `_get_bucket_memory_offset`; no `bulk_buffer_size`/`lcd_resolution` | **stands down** (stock driver) | Z53/Z63/Z73 only |
| 1.14.0 | reference | active, verified | + Kraken 2023, 2023 Elite |
| 1.15.0 | identical to 1.14.0 | active, verified (development hardware) | + Kraken 2024 Elite RGB |
| 1.16.0 | identical to 1.14.0 | active, verified | + Kraken 2024 Plus |
| main (2026-09) | identical to 1.14.0 | active (CI canary) | as 1.16.0 |

Distribution packages (2026-09): Arch, Fedora 42+, Debian testing, Alpine
3.24, NixOS 26.05 and Gentoo ship 1.16.0; Debian 13, Ubuntu 25.10/26.04,
Fedora 40/41 and NixOS 25.x ship 1.15.0; Ubuntu 25.04 ships 1.14.0;
Ubuntu 24.04, Alpine ≤ 3.22 and NixOS ≤ 24.11 ship 1.13.0 (patch stands
down). openSUSE has no package; the README describes the pip route.

On 1.13.0 the patch used to activate although the driver had neither
`bulk_buffer_size` nor the same upload code — the old check only looked
for one log-message string. The first real upload would have failed with
an `AttributeError`. The contract closes that gap.

Note that the `_send_data` bug the patch works around is present in every
release including 1.16.0 and in upstream main; see [UPSTREAM.md](UPSTREAM.md)
for the proposed fix.

## What CI does

- `tests` matrix: Python 3.11/3.12/3.13/3.14 × liquidctl 1.14.0/1.15.0/1.16.0
  (`VERIFIED_RELEASES`). On these, `test_upstream.py` **fails** if the
  patch cannot activate, so stale fingerprints are caught. 1.13.0 runs
  once to prove the clean fallback.
- `upstream-canary`: installs liquidctl from git `main`, prints the
  fingerprints and runs the suite. Weekly and on every push. A red canary
  is a heads-up that the next liquidctl release will stand the patch
  down; it does not fail the build.
- `lint`: `ruff check .`

## When a liquidctl release changes an internal

1. Run `python -m kraken_lcd.upstream` against the new version. It prints
   every fingerprint and marks the ones that differ.
2. Diff the changed method(s) between the old and the new liquidctl
   (`liquidctl/driver/kraken3.py`). Decide:
   - Cosmetic or unrelated change (should not happen: the fingerprint
     ignores those): nothing to do beyond step 4.
   - Protocol or logic change in a method the patch *calls*: check the
     patched `_send_data` still uses it correctly; adjust if not.
   - Change in `_send_data` itself: re-derive the patched version from
     the new upstream code (it must stay byte-identical on the wire,
     apart from the verified setup). If upstream fixed the bug, the patch
     is no longer needed on that version, see below.
3. Test on hardware: `kraken-lcd doctor` (patch active, device found),
   then `kraken-lcd run` for a few carousel cycles and check the journal
   for `upload health` and refusals.
4. Update `CONTRACT` fingerprints and `VERIFIED_RELEASES` in
   `kraken_lcd/upstream.py`, the CI matrix, the table above, and the
   changelog.

## Exit strategy

The patch is meant to disappear. When upstream merges the fix from
[UPSTREAM.md](UPSTREAM.md), the `_send_data` fingerprint changes, the
patch stands down automatically, and the stock driver then raises on a
refused bucket setup instead of streaming. The device layer already
handles an exception from an upload like any other failed attempt (full
memory clear, retry, reconnect); only the flash-free soft clear is lost,
which is cosmetic. At that point the patch can be dropped for liquidctl
versions that contain the fix.
