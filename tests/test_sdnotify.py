import socket

from kraken_lcd.sdnotify import SystemdNotifier


def test_disabled_without_notify_socket(monkeypatch):
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    notifier = SystemdNotifier()
    assert notifier.enabled is False
    notifier.ready()  # all no-ops, no exception
    notifier.watchdog()
    notifier.stopping()


def test_messages_arrive_at_the_socket(tmp_path):
    path = str(tmp_path / "notify.sock")
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as server:
        server.bind(path)
        server.settimeout(2)
        notifier = SystemdNotifier(socket_path=path)
        assert notifier.enabled
        notifier.ready()
        notifier.watchdog()
        notifier.stopping()
        received = [server.recv(64) for _ in range(3)]
    assert received == [b"READY=1", b"WATCHDOG=1", b"STOPPING=1"]


def test_abstract_namespace_translation():
    name = "\0kraken-lcd-test-abstract"
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as server:
        server.bind(name)
        server.settimeout(2)
        notifier = SystemdNotifier(socket_path="@kraken-lcd-test-abstract")
        notifier.watchdog()
        assert server.recv(64) == b"WATCHDOG=1"


def test_send_failure_is_swallowed(tmp_path):
    notifier = SystemdNotifier(socket_path=str(tmp_path / "gone.sock"))
    notifier.ready()  # no listener -> OSError internally, but no exception


def test_env_socket_is_picked_up(monkeypatch, tmp_path):
    path = str(tmp_path / "env.sock")
    monkeypatch.setenv("NOTIFY_SOCKET", path)
    assert SystemdNotifier().enabled is True
