"""The https scenario's TlsProbe records what a client sent before the
client can see its connection close. It closed first, then recorded: a
client whose handshake failed on the close could read the probe before the
record and find nothing (test_connect_via_tcp_with_https_scheme, once)."""

from __future__ import annotations

import socket
import threading
import time

from .steps._grpc_backend import TlsProbe


class _LateLock:
    """The probe's lock, taken late by the probe's own thread: the window
    between the probe answering a connection and recording it."""

    def __init__(self, probe: TlsProbe) -> None:
        self._probe = probe
        self._lock = threading.Lock()

    def __enter__(self):
        if threading.current_thread() is self._probe._thread:
            time.sleep(0.3)
        return self._lock.__enter__()

    def __exit__(self, *exc):
        return self._lock.__exit__(*exc)


def test_the_probe_records_the_first_bytes_before_the_client_sees_the_close():
    probe = TlsProbe()
    probe._lock = _LateLock(probe)
    try:
        with socket.create_connection(("127.0.0.1", probe.port), timeout=5) as conn:
            conn.sendall(b"\x16\x03\x01hello, probe")
            closed = conn.recv(1) == b""
        assert closed, "the probe sent something back"
        assert probe.first_bytes() == [b"\x16\x03\x01hello, probe"[:16]]
    finally:
        probe.close()
