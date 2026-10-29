"""Serve a ComponentHost as a child process of an in-app angzarr runtime.

angzarr-embedded (the Rust runtime that runs inside an application) launches
a business process written in Python as a child process (by default
``uv run --locked <entry point>``, overridable in its configuration) and
talks to it over gRPC on a Unix socket. The child is an ordinary
:class:`~angzarr_client.host.ComponentHost`; :func:`run_child` adds the
launch contract.

The handshake (version 1)
-------------------------

Once the host is SERVING, the child writes exactly one line to its stdout::

    angzarr-handshake v1 unix:<absolute-socket-path>\\n

and nothing else, ever: before the line nothing is written to stdout, and
after it the child's stdout is closed (the parent reads the line, then end
of file). Logs and any stray ``print`` go to stderr, because
:func:`run_child` points file descriptor 1 at stderr before it starts the
host. A child that exits without writing the line failed to start; its
stderr says why.

- The socket path is ``socket_path`` when given, else the ``ANGZARR_SOCKET``
  environment variable (made absolute), else a fresh path in the system
  temporary directory.
- The parent then calls ``ComponentService.Describe`` and the component's
  kind service (``CommandHandlerService.Handle`` for an aggregate) on that
  address.
- The child serves until its stdin reaches end of file (the parent closed it
  or exited) or it receives SIGTERM or SIGINT. It then stops the host
  gracefully, removes the socket file and returns.

A parent parses the line with :func:`parse_handshake`; ``v1`` is the format
version, and a later format changes it.
"""

from __future__ import annotations

from .host import ComponentHost

#: The environment variable naming the socket path the parent chose.
SOCKET_ENV = "ANGZARR_SOCKET"

#: Everything on the handshake line before the address.
HANDSHAKE_PREFIX = "angzarr-handshake v1 "


def handshake_line(address: str) -> str:
    """The handshake line announcing ``address`` (``unix:<path>``),
    newline included."""
    raise NotImplementedError("red: handshake_line")


def parse_handshake(line: str) -> str:
    """The address a handshake line announces. A line that is not a v1
    handshake raises :class:`~angzarr_client.host.ConfigurationError` with
    code ``HANDSHAKE_MALFORMED`` and the line in its details."""
    raise NotImplementedError("red: parse_handshake")


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
    raise NotImplementedError("red: run_child")
