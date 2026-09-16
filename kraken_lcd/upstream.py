"""The liquidctl upstream contract behind the driver patch.

``kraken_lcd.driver_patch`` subclasses liquidctl's ``KrakenZ3`` and calls,
overrides or reimplements *private* methods of it. Private API may change
in any release, so the patch is only allowed to activate when every
internal it depends on is exactly the code it was written against:

- the method exists and has the same call signature, and
- its body has the same token stream — comments, docstrings and
  formatting are ignored, any change to the logic is not.

Everything else stands the patch down: the daemon then runs on the stock
driver, which works but without verified bucket setups and without the
flash-free memory cleanup. The reason is logged once and shown by
``kraken-lcd doctor``.

``VERIFIED_RELEASES`` lists the liquidctl releases this contract has been
checked against on real hardware or in CI. A release that is not listed
but matches every fingerprint (e.g. a Debian backport or a release that
only added devices) is accepted as well — the fingerprints, not the
version number, are the contract. The update procedure for a liquidctl
release that changes one of the internals is in
``docs/liquidctl-compatibility.md``.
"""

import functools
import hashlib
import inspect
import io
import textwrap
import tokenize
from dataclasses import dataclass

# liquidctl releases whose KrakenZ3 internals were verified to match the
# contract below (CI runs the full test suite against each of them)
VERIFIED_RELEASES = ("1.14.0", "1.15.0", "1.16.0")

# KrakenZ3.__init__ parameters the patch (and the device layer) rely on;
# both appeared in 1.14.0 together with the 2023/2024 models
REQUIRED_INIT_PARAMETERS = ("bulk_buffer_size", "lcd_resolution")


@dataclass(frozen=True)
class Internal:
    """One private KrakenZ3 method the patch depends on."""
    name: str
    signature: str    # str(inspect.signature(method))
    fingerprint: str  # fingerprint() of the method, see below
    role: str         # why the patch needs it (documentation only)


# Fingerprints of liquidctl 1.14.0; identical in 1.15.0, 1.16.0 and in
# upstream main as of 2026-09 (regenerate with ``python -m kraken_lcd.upstream``).
CONTRACT: tuple[Internal, ...] = (
    Internal("_send_data", "(self, data, bulkInfo)", "d5e5c24ef5db",
             "replaced by the patched upload path; must be the version the "
             "patch reimplements byte-for-byte on the wire"),
    Internal("_setup_bucket",
             "(self, startBucketIndex, endBucketIndex, startingMemoryAddress, memorySize)",
             "4974112bcbce", "called; its True/False result gates the upload"),
    Internal("_switch_bucket", "(self, bucketIndex, mode=4)", "f2c697b989ea",
             "overridden to track the displayed bucket; mode 0x4 = show, 0x2 = liquid view"),
    Internal("_delete_all_buckets", "(self)", "4eb5899b015e",
             "overridden to reset the tracked bucket; known to flash the LCD"),
    Internal("_delete_bucket", "(self, bucketIndex)", "e7362cf8ed3b",
             "called by the flash-free soft clear"),
    Internal("_query_buckets", "(self)", "6049b3e7008e",
             "called; the soft clear parses its 64-byte per-bucket responses"),
    Internal("_find_next_unoccupied_bucket", "(self, buckets)", "7b3317c8d1bb",
             "called; defines the occupancy convention (bytes 15+ all zero)"),
    Internal("_prepare_bucket", "(self, bucketIndex, bucketFilled)", "6e45d4e9e696",
             "called by the patched upload path"),
    Internal("_get_bucket_memory_offset", "(self, buckets, bucketIndex, dataSize)",
             "dee3d4c9bb40", "called; -1 means 'no gap, clear and start over'"),
    Internal("_bulk_write", "(self, data)", "22d8977bc209",
             "called for the header and every data chunk"),
    Internal("_write", "(self, data)", "0ca907059fac", "called for the end-of-transfer report"),
    Internal("_write_then_read", "(self, data)", "47c5382899bf",
             "overridden: reads until the matching reply instead of taking the "
             "next report; every bucket command above goes through it"),
)


@dataclass(frozen=True)
class Compatibility:
    """Result of checking the installed liquidctl against the contract."""
    liquidctl_version: str
    problems: tuple[str, ...] = ()
    verified_release: bool = False

    @property
    def compatible(self) -> bool:
        return not self.problems

    def summary(self) -> str:
        """One line for the log; ``problems`` carries the details."""
        if self.compatible:
            note = ("verified release" if self.verified_release
                    else "not a verified release, but every internal matches")
            return (f"liquidctl {self.liquidctl_version}: all {len(CONTRACT)} "
                    f"KrakenZ3 internals match the contract ({note})")
        return (f"liquidctl {self.liquidctl_version}: "
                + "; ".join(self.problems))


def fingerprint(function) -> str:
    """Stable fingerprint of a function's logic.

    Hashes the token stream of the function's source with comments, the
    docstring and blank lines removed. Indentation and statement
    boundaries are kept as structural tokens, so moving a statement in or
    out of a block changes the fingerprint while re-wrapping lines inside
    brackets does not. Anything that adds or removes a token — a trailing
    comma, extra parentheses, a renamed variable — counts as a change:
    deliberately conservative, a false "changed" only costs the patch,
    a false "unchanged" could cost the device. Tokens (unlike
    ``ast.dump``) are stable across Python versions, which matters more
    here: a Python upgrade must not silently disable the safe upload path.

    Raises ``OSError``/``TypeError`` when the source is not available.
    """
    source = textwrap.dedent(inspect.getsource(function))
    tokens: list[tuple[int, str]] = []
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type in (tokenize.COMMENT, tokenize.NL, tokenize.ENCODING,
                        tokenize.ENDMARKER):
            continue
        if tok.type in (tokenize.INDENT, tokenize.DEDENT, tokenize.NEWLINE):
            tokens.append((tok.type, tokenize.tok_name[tok.type]))
        elif tokens and tokens[-1] == (tokenize.NAME, "def"):
            tokens.append((tok.type, "<name>"))  # the name is checked separately
        else:
            tokens.append((tok.type, tok.string))
    # the docstring is the first statement of the body: INDENT STRING NEWLINE
    for i, (kind, _text) in enumerate(tokens):
        if kind == tokenize.INDENT:
            if (i + 2 < len(tokens) and tokens[i + 1][0] == tokenize.STRING
                    and tokens[i + 2][0] == tokenize.NEWLINE):
                del tokens[i + 1:i + 3]
            break
    stream = "\n".join(text for _kind, text in tokens)
    return hashlib.sha256(stream.encode()).hexdigest()[:12]


def check(driver_class, version: str = "unknown") -> Compatibility:
    """Check *driver_class* (normally KrakenZ3) against the contract."""
    problems: list[str] = []
    try:
        init_params = inspect.signature(driver_class.__init__).parameters
    except (TypeError, ValueError):
        init_params = {}
    for name in REQUIRED_INIT_PARAMETERS:
        if name not in init_params:
            problems.append(f"{driver_class.__name__}.__init__ has no "
                            f"'{name}' parameter (liquidctl < 1.14?)")
    for internal in CONTRACT:
        label = f"{driver_class.__name__}.{internal.name}"
        method = getattr(driver_class, internal.name, None)
        if method is None:
            problems.append(f"{label} is gone")
            continue
        try:
            signature = str(inspect.signature(method))
        except (TypeError, ValueError):
            signature = "<unavailable>"
        if signature != internal.signature:
            problems.append(f"{label} signature changed: {signature} "
                            f"(expected {internal.signature})")
            continue
        try:
            actual = fingerprint(method)
        except (OSError, TypeError) as exc:
            problems.append(f"{label}: source not available for verification ({exc})")
            continue
        if actual != internal.fingerprint:
            problems.append(f"{label} changed upstream (fingerprint {actual}, "
                            f"expected {internal.fingerprint})")
    return Compatibility(liquidctl_version=version, problems=tuple(problems),
                         verified_release=version in VERIFIED_RELEASES)


@functools.cache
def installed() -> Compatibility:
    """Check the installed liquidctl (once per process)."""
    try:
        import liquidctl
        from liquidctl.driver.kraken3 import KrakenZ3
    except Exception as exc:  # ImportError, or a broken installation
        return Compatibility("unknown", (f"liquidctl could not be imported: {exc}",))
    return check(KrakenZ3, str(getattr(liquidctl, "__version__", "unknown")))


def _main() -> int:
    """Print the installed liquidctl's fingerprints (maintainer tool).

    Used to refresh CONTRACT after auditing a liquidctl change; see
    docs/liquidctl-compatibility.md.
    """
    import liquidctl
    from liquidctl.driver.kraken3 import KrakenZ3
    print(f"liquidctl {liquidctl.__version__}")
    for internal in CONTRACT:
        method = getattr(KrakenZ3, internal.name, None)
        if method is None:
            print(f"  {internal.name:30s} MISSING")
            continue
        actual = fingerprint(method)
        marker = "" if actual == internal.fingerprint else "   <-- differs from the contract"
        print(f"  {internal.name:30s} {actual}  {inspect.signature(method)}{marker}")
    result = installed()
    print(result.summary())
    return 0 if result.compatible else 3


if __name__ == "__main__":
    raise SystemExit(_main())
