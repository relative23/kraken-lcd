"""Disk cache for rendered GIFs with LRU pruning by file mtime."""

import hashlib
import logging
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)


class RenderCache:
    def __init__(self, directory: Path, max_bytes: int) -> None:
        self.max_bytes = max_bytes
        self.directory = self._prepare_dir(directory)

    @staticmethod
    def _prepare_dir(directory: Path) -> Path:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            probe = directory / ".write-test"
            probe.touch()
            probe.unlink()
            return directory
        except OSError:
            fallback = Path.home() / ".cache" / "kraken-lcd"
            fallback.mkdir(parents=True, exist_ok=True)
            log.warning("cache dir %s not writable, using %s", directory, fallback)
            return fallback

    def path_for(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode()).hexdigest()[:24]
        return self.directory / f"{digest}.gif"

    def get_or_render(self, key: str, render: Callable[[Path], None]) -> Path | None:
        """Return the cached GIF for *key*, rendering it on a miss."""
        path = self.path_for(key)
        if path.is_file() and path.stat().st_size > 0:
            path.touch()  # refresh mtime so LRU pruning keeps hot entries
            return path
        try:
            render(path)
        except Exception:
            log.exception("rendering %s failed", path.name)
            path.unlink(missing_ok=True)
            return None
        self.prune()
        if path.is_file() and path.stat().st_size > 0:
            return path
        return None

    def prune(self) -> None:
        """Delete oldest entries (by mtime) until the cache fits max_bytes."""
        entries = []
        total = 0
        for path in self.directory.glob("*.gif"):
            try:
                stat = path.stat()
            except OSError:
                continue
            entries.append((stat.st_mtime, stat.st_size, path))
            total += stat.st_size
        if total <= self.max_bytes:
            return
        entries.sort()  # oldest first
        for _mtime, size, path in entries:
            path.unlink(missing_ok=True)
            total -= size
            log.info("cache prune: removed %s", path.name)
            if total <= self.max_bytes:
                break
