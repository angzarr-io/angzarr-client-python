"""Serve a ComponentHost as a child process of an in-app angzarr runtime.

angzarr-embedded (the Rust runtime that runs inside an application) launches
a business process written in Python as a child process (by default
``uv run --locked <entry point>``, overridable in its configuration) and
talks to it over gRPC on a Unix socket. The child is an ordinary
:class:`~angzarr_client.host.ComponentHost`; :func:`run_child` adds the
launch contract.

The launch protocol (version 1)
------------------------------

The protocol is angzarr-project's ``docs/design/child-protocol.md``
(decision 14.59), the same for every language; ``parity/client/child.feature``
is its scenarios, which this package runs against :func:`run_child`. In
short: once the host is SERVING (gRPC health, the overall server), the child
writes one line to its stdout::

    angzarr-handshake v1 unix:<absolute-socket-path>\\n

The protocol lets a child write lines that are not handshakes before it
and anything after it; :func:`run_child` writes nothing else to stdout at
all: logs and any stray ``print`` go to stderr, because it points file
descriptor 1 at stderr before it starts the host, and it closes its stdout
after the line (the parent reads the line, then end of file). A child that
exits without writing the line failed to start; its stderr says why.

- The socket path is ``socket_path`` when given, else the ``ANGZARR_SOCKET``
  environment variable (made absolute), else a fresh path in the system
  temporary directory.
- The parent then calls ``ComponentService.Describe`` and the component's
  kind service (``CommandHandlerService.Handle`` for an aggregate) on that
  address.
- The child serves until its stdin reaches end of file (the parent closed it
  or exited) or it receives SIGTERM or SIGINT. It then stops the host
  gracefully, removes the socket file and returns.

A parent reads the line with :func:`parse_handshake`, which classifies a
line by its first word as the protocol does: not a handshake (``None``), a
handshake (its address), or malformed (refused under
``HOST_REASON_HANDSHAKE_MALFORMED``). ``v1`` is the format version, and a
later format changes it.
"""

from __future__ import annotations

import os
import secrets
import signal
import sys
import tempfile
import threading

import structlog
from grpc_health.v1 import health_pb2

from .error_codes import codes
from .host import ComponentHost, ConfigurationError
from .proto.io.angzarr.v1 import reason_args

#: The environment variable naming the socket path the parent chose.
SOCKET_ENV = "ANGZARR_SOCKET"

#: The handshake line's first word.
HANDSHAKE_PREFIX = "angzarr-handshake"

#: The handshake format version this package writes and reads.
PROTOCOL_VERSION = "v1"

_UNIX = "unix:"
_SERVING = health_pb2.HealthCheckResponse.SERVING


def handshake_line(address: str) -> str:
    """The handshake line announcing ``address`` (``unix:<path>``),
    newline included."""
    return f"{HANDSHAKE_PREFIX} {PROTOCOL_VERSION} {address}\n"


def parse_handshake(line: str) -> str | None:
    """The address (``unix:<path>``) a handshake line announces, or ``None``
    for a line that is not a handshake: one whose first word (everything
    before its first space) is not ``angzarr-handshake``, the empty line
    included. Its line ending (LF or CR LF) is not part of it, and the path
    is the rest of the line, spaces included.

    A line whose first word is ``angzarr-handshake`` but which is not a v1
    handshake naming an absolute Unix socket path raises
    :class:`~angzarr_client.host.ConfigurationError` with code
    ``HOST_REASON_HANDSHAKE_MALFORMED`` and details ``line``, the line with
    its line ending removed: it is refused, never skipped."""
    body = line.removesuffix("\n").removesuffix("\r")
    first, _, rest = body.partition(" ")
    if first != HANDSHAKE_PREFIX:
        return None
    # A missing space leaves the version or the address empty, which the
    # checks below refuse.
    version, _, address = rest.partition(" ")
    if (
        version != PROTOCOL_VERSION
        or not address.startswith(_UNIX)
        or not os.path.isabs(address.removeprefix(_UNIX))
        or "\n" in address
    ):
        raise ConfigurationError(
            codes.HOST_REASON_HANDSHAKE_MALFORMED,
            details={reason_args.LINE: body},
        )
    return address


def resolve_socket_path(given: str | None = None) -> str:
    """The absolute socket path a child binds: ``given``, else
    ``$ANGZARR_SOCKET``, else a fresh path in the temporary directory."""
    path = given or os.environ.get(SOCKET_ENV)
    if not path:
        name = f"angzarr-{os.getpid()}-{secrets.token_hex(4)}.sock"
        path = os.path.join(tempfile.gettempdir(), name)
    return os.path.abspath(path)


def _watch_stdin(stopping: threading.Event) -> None:
    """Set ``stopping`` once stdin reaches end of file (or cannot be read)."""
    try:
        while os.read(0, 4096):
            pass
    except OSError:
        pass
    stopping.set()


def run_child(
    host: ComponentHost,
    *,
    socket_path: str | None = None,
    grace: float = 5.0,
) -> None:
    """Serve ``host`` on a Unix socket under the child-process contract
    (the module docstring): handshake on stdout once SERVING, then serve
    until stdin closes or SIGTERM / SIGINT, then stop within ``grace``
    seconds."""
    log = structlog.get_logger(__name__)
    # Keep the real stdout for the handshake alone; everything else written
    # to file descriptor 1 from here on (logs, stray prints) goes to stderr.
    sys.stdout.flush()
    handshake_fd = os.dup(1)
    os.dup2(2, 1)
    stopping = threading.Event()

    def _request_stop(signum, frame):
        stopping.set()

    previous = {
        sig: signal.signal(sig, _request_stop)
        for sig in (signal.SIGTERM, signal.SIGINT)
    }
    threading.Thread(
        target=_watch_stdin, args=(stopping,), name="angzarr-child-stdin", daemon=True
    ).start()
    try:
        address = host.start(_UNIX + resolve_socket_path(socket_path))
        # Python runs signal handlers on the main thread between bytecodes;
        # timed waits return to the interpreter so a signal is seen promptly.
        while host.health_status() != _SERVING:
            if stopping.wait(0.01):
                return
        log.info("child_serving", address=address)
        os.write(handshake_fd, handshake_line(address).encode())
        os.close(handshake_fd)
        handshake_fd = -1
        while not stopping.wait(0.2):
            pass
    finally:
        if handshake_fd >= 0:
            os.close(handshake_fd)
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        host.stop(grace)
