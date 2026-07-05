"""Minimal sd_notify client (no external dependency).

Speaks the systemd notification protocol over the AF_UNIX datagram socket
in ``$NOTIFY_SOCKET``. When that variable is absent (foreground runs,
tests), every call is a silent no-op.
"""

import logging
import os
import socket

log = logging.getLogger(__name__)


class SystemdNotifier:
    def __init__(self, socket_path: str | None = None) -> None:
        self._address = socket_path or os.environ.get("NOTIFY_SOCKET")
        if self._address and self._address.startswith("@"):
            # abstract socket namespace
            self._address = "\0" + self._address[1:]

    @property
    def enabled(self) -> bool:
        return self._address is not None

    def ready(self) -> None:
        self._send("READY=1")

    def watchdog(self) -> None:
        self._send("WATCHDOG=1")

    def stopping(self) -> None:
        self._send("STOPPING=1")

    def _send(self, message: str) -> None:
        if self._address is None:
            return
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
                sock.connect(self._address)
                sock.send(message.encode())
        except OSError as exc:
            # never let notification plumbing take the carousel down
            log.debug("sd_notify %s failed: %s", message, exc)
