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
(decisions 14.59 and 14.62), the same for every language; ``parity/client/child.feature``
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
  temporary directory. It is UTF-8: given one that is not, the child binds
  nothing, writes no handshake and fails (``HOST_REASON_HANDSHAKE_MALFORMED``,
  the line it would have written given back escaped).
- The parent then calls ``ComponentService.Describe`` and the component's
  kind service (``CommandHandlerService.Handle`` for an aggregate) on that
  address.
- The child serves until its stdin reaches end of file (the parent closed it
  or exited) or it receives SIGTERM or SIGINT. It then stops the host
  gracefully, removes the socket file and returns.

A parent reads the line with :func:`parse_handshake`, which classifies a
line by its first word as the protocol does: not a handshake (``None``), a
handshake (its address), or malformed (refused under
``HOST_REASON_HANDSHAKE_MALFORMED``), a path that is not UTF-8 included; it
takes the line as text or as the bytes read. ``v1`` is the format version,
and a later format changes it.
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
from .proto.io.angzarr.transport.embedded.v1 import child_protocol_pb2, reason_args

#: The child launch protocol's constants: the spec's, read from the
#: ``(child_protocol)`` option of the local-process transport's
#: child_protocol.proto (decision 14.64), never typed here.
_PROTOCOL = child_protocol_pb2.DESCRIPTOR.GetOptions().Extensions[
    child_protocol_pb2.child_protocol
]

#: The environment variable naming the socket path the parent chose.
SOCKET_ENV = _PROTOCOL.socket_env

#: The handshake line's first word.
HANDSHAKE_PREFIX = _PROTOCOL.handshake_prefix

#: The handshake format version this package writes and reads.
PROTOCOL_VERSION = _PROTOCOL.protocol_version

#: The scheme before the socket path a handshake names.
ADDRESS_SCHEME = _PROTOCOL.address_scheme

_UNIX = ADDRESS_SCHEME
_SERVING = health_pb2.HealthCheckResponse.SERVING


def handshake_line(address: str) -> str:
    """The handshake line announcing ``address`` (``unix:<path>``),
    newline included."""
    return f"{HANDSHAKE_PREFIX} {PROTOCOL_VERSION} {address}\n"


def escaped(raw: bytes) -> str:
    """``raw`` as a refused line's text (the protocol's section 2): as it is
    when it is UTF-8, else each byte outside printable ASCII, and each
    backslash, written ``\\xhh``, so no byte is lost or decoded into
    another character."""
    try:
        return raw.decode()
    except UnicodeDecodeError:
        return "".join(
            chr(b) if 0x20 <= b <= 0x7E and b != 0x5C else f"\\x{b:02x}" for b in raw
        )


def _malformed(line: str) -> ConfigurationError:
    return ConfigurationError(
        codes.HOST_REASON_HANDSHAKE_MALFORMED, details={reason_args.LINE: line}
    )


def parse_handshake(line: str | bytes) -> str | None:
    """The address (``unix:<path>``) a handshake line announces, or ``None``
    for a line that is not a handshake: one whose first word (everything
    before its first space) is not ``angzarr-handshake``, the empty line
    included. Its line ending (LF or CR LF) is not part of it, and the path
    is the rest of the line, spaces included. ``line`` is text, or the
    bytes read, whose path must be UTF-8.

    A line whose first word is ``angzarr-handshake`` but which is not a v1
    handshake naming an absolute UTF-8 Unix socket path raises
    :class:`~angzarr_client.host.ConfigurationError` with code
    ``HOST_REASON_HANDSHAKE_MALFORMED`` and details ``line``, the line with
    its line ending removed (:func:`escaped`, when it is bytes that are not
    UTF-8): it is refused, never skipped."""
    if isinstance(line, bytes):
        raw = line.removesuffix(b"\n").removesuffix(b"\r")
        if raw.partition(b" ")[0] != HANDSHAKE_PREFIX.encode():
            return None
        try:
            line = raw.decode()
        except UnicodeDecodeError:
            raise _malformed(escaped(raw)) from None
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
        raise _malformed(body)
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
    address = _UNIX + resolve_socket_path(socket_path)
    try:
        address.encode()
    except UnicodeEncodeError:
        # A path the os module gave as text with its undecodable bytes
        # escaped: no handshake could name it, so nothing is bound.
        line = handshake_line(address).encode(errors="surrogateescape")
        raise _malformed(escaped(line.removesuffix(b"\n"))) from None
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
        address = host.start(address)
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
