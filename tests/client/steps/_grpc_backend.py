"""In-process gRPC coordinator backend for connection-level step files.

:class:`GrpcBackend` is a real ``grpc.server`` serving the coordinator
methods the connection scenarios call, bound either to a localhost TCP
port or to a Unix socket path. Every RPC is recorded with the method
name and the transport peer the server saw, so tests can assert which
transport a call arrived on and how many distinct client connections
carried the traffic.

Peers are reported by gRPC core as ``ipv4:127.0.0.1:<port>`` /
``ipv6:[::1]:<port>`` for TCP (the client's ephemeral port, unique per
HTTP/2 connection) and ``unix:...`` for Unix domain sockets.

:class:`TlsProbe` is a raw TCP listener that records the first bytes a
client sends, which distinguishes a TLS ClientHello from plaintext
HTTP/2.
"""

from __future__ import annotations

import socket
import threading
from concurrent import futures
from dataclasses import dataclass
from typing import Optional

import grpc

from angzarr_client._pb import (
    CommandResponse,
    EventBook,
    EventPage,
    Projection,
)
from angzarr_client.proto.io.angzarr.v1 import (
    command_handler_pb2_grpc,
    projector_pb2_grpc,
    query_pb2_grpc,
)

SPECULATIVE_PROJECTOR = "order-summary"


@dataclass(frozen=True)
class Rpc:
    method: str
    peer: str


class _Recorder:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rpcs: list[Rpc] = []

    def record(self, method: str, context: grpc.ServicerContext) -> None:
        with self._lock:
            self._rpcs.append(Rpc(method, context.peer()))

    def snapshot(self) -> list[Rpc]:
        with self._lock:
            return list(self._rpcs)


class _EventQuery(query_pb2_grpc.EventQueryServiceServicer):
    def __init__(self, recorder: _Recorder) -> None:
        self._rec = recorder

    def GetEventBook(self, request, context):
        self._rec.record("GetEventBook", context)
        return EventBook(cover=request.cover)


class _CommandHandlerCoordinator(
    command_handler_pb2_grpc.CommandHandlerCoordinatorServiceServicer
):
    def __init__(self, recorder: _Recorder) -> None:
        self._rec = recorder

    def HandleCommand(self, request, context):
        self._rec.record("HandleCommand", context)
        book = EventBook(cover=request.command.cover)
        for _ in request.command.pages:
            book.pages.append(EventPage())
        return CommandResponse(events=book)


class _ProjectorCoordinator(projector_pb2_grpc.ProjectorCoordinatorServiceServicer):
    def __init__(self, recorder: _Recorder) -> None:
        self._rec = recorder

    def HandleSpeculative(self, request, context):
        self._rec.record("HandleSpeculative", context)
        return Projection(
            cover=request.events.cover,
            projector=SPECULATIVE_PROJECTOR,
            sequence=len(request.events.pages),
        )


class GrpcBackend:
    """A running coordinator backend on one TCP port or one Unix socket."""

    def __init__(
        self,
        server: grpc.Server,
        recorder: _Recorder,
        port: Optional[int],
        socket_path: Optional[str],
    ) -> None:
        self._server = server
        self._rec = recorder
        self.port = port
        self.socket_path = socket_path

    @classmethod
    def _build(cls) -> tuple[grpc.Server, _Recorder]:
        rec = _Recorder()
        server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
        query_pb2_grpc.add_EventQueryServiceServicer_to_server(_EventQuery(rec), server)
        command_handler_pb2_grpc.add_CommandHandlerCoordinatorServiceServicer_to_server(
            _CommandHandlerCoordinator(rec), server
        )
        projector_pb2_grpc.add_ProjectorCoordinatorServiceServicer_to_server(
            _ProjectorCoordinator(rec), server
        )
        return server, rec

    @classmethod
    def start_tcp(cls, port: int = 0) -> "GrpcBackend":
        """Listen on ``localhost:<port>``; ``0`` picks a free port."""
        server, rec = cls._build()
        bound = server.add_insecure_port(f"localhost:{port}")
        if bound == 0:
            raise RuntimeError(f"could not bind localhost:{port}")
        server.start()
        return cls(server, rec, bound, None)

    @classmethod
    def start_uds(cls, path: str) -> "GrpcBackend":
        """Listen only on the Unix socket at absolute ``path``."""
        server, rec = cls._build()
        if server.add_insecure_port(f"unix://{path}") == 0:
            raise RuntimeError(f"could not bind unix socket {path}")
        server.start()
        return cls(server, rec, None, path)

    def stop(self) -> None:
        """Stop serving and drop every open connection."""
        self._server.stop(grace=None).wait()

    def rpcs(self) -> list[Rpc]:
        return self._rec.snapshot()

    def methods(self) -> list[str]:
        return [r.method for r in self.rpcs()]

    def peers(self) -> set[str]:
        """Distinct transport peers that carried RPCs."""
        return {r.peer for r in self.rpcs()}


def unused_tcp_port() -> int:
    """A localhost port with nothing listening on it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TlsProbe:
    """Raw TCP listener recording the first bytes of each inbound connection."""

    def __init__(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self._sock.settimeout(0.2)
        self.port = self._sock.getsockname()[1]
        self._lock = threading.Lock()
        self._first_bytes: list[bytes] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with conn:
                conn.settimeout(2)
                try:
                    data = conn.recv(16)
                except OSError:
                    data = b""
            with self._lock:
                self._first_bytes.append(data)

    def first_bytes(self) -> list[bytes]:
        with self._lock:
            return list(self._first_bytes)

    def close(self) -> None:
        self._stop.set()
        self._sock.close()
        self._thread.join(timeout=2)


def is_tls_client_hello(data: bytes) -> bool:
    """TLS record header: handshake (0x16), version major 0x03."""
    return len(data) >= 2 and data[0] == 0x16 and data[1] == 0x03


HTTP2_PREFACE = b"PRI * HTTP/2.0"
