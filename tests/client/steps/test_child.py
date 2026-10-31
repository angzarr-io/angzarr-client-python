"""Step defs for parity/client/child.feature: the child-process launch
protocol (docs/design/child-protocol.md in angzarr-project, decision 14.59),
against this package's child shim, :func:`angzarr_client.child.run_child`,
and its handshake parser, :func:`angzarr_client.child.parse_handshake`.

The child is tests/client/child_order.py, launched as a host launches it:
stdin and stdout piped, stderr captured, the socket named in
``ANGZARR_SOCKET``. Its stdout is read from the raw pipe, so a step can tell
at once whether anything has been written (C-0750) without blocking.
"""

from __future__ import annotations

import os
import pathlib
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field

import grpc
import pytest
from google.protobuf import wrappers_pb2
from grpc_health.v1 import health_pb2, health_pb2_grpc
from pytest_bdd import given, parsers, scenarios, then, when

from angzarr_client.child import SOCKET_ENV, parse_handshake
from angzarr_client.host import ConfigurationError
from angzarr_client.proto.io.angzarr.v1 import (
    command_handler_pb2_grpc,
    component_pb2,
    component_pb2_grpc,
    types_pb2,
)
from angzarr_client.router import pack

from ..child_order import GATE_ENV

scenarios("parity/client/child.feature")

REPO = pathlib.Path(__file__).resolve().parents[3]
# How long a step waits for the child to do what it must; a deadline for a
# failing child, never a wait for an interleaving.
DEADLINE = 30.0
FIRST_WORD = "angzarr-handshake"


def _is_handshake_line(line: str) -> bool:
    """Whether ``line``'s first word (before its first space) is
    angzarr-handshake: what a reader treats as a handshake, well formed or
    not."""
    return line.rstrip("\r\n").split(" ", 1)[0] == FIRST_WORD


@dataclass
class _World:
    tmp: pathlib.Path
    held: bool = False
    proc: subprocess.Popen | None = None
    socket_path: str = ""
    buffer: bytes = b""
    # Every complete stdout line read so far, and the handshake's index.
    lines: list[str] = field(default_factory=list)
    handshake_at: int | None = None
    address: str = ""
    exit_code: int | None = None
    stderr: str = ""
    parsed: str | None = None
    error: Exception | None = None
    channels: list[grpc.Channel] = field(default_factory=list)

    @property
    def gate(self) -> pathlib.Path:
        return self.tmp / "gate"

    def channel(self) -> grpc.Channel:
        ch = grpc.insecure_channel(f"unix:{self.socket_path}")
        self.channels.append(ch)
        return ch

    def _fill(self, timeout: float) -> bool:
        """Read what the child's stdout has within ``timeout`` (0: only what
        is there now) into complete lines; False at end of file."""
        fd = self.proc.stdout.fileno()
        sel = selectors.DefaultSelector()
        sel.register(fd, selectors.EVENT_READ)
        try:
            if not sel.select(timeout=timeout):
                return True
        finally:
            sel.close()
        chunk = os.read(fd, 65536)
        if not chunk:
            return False
        self.buffer += chunk
        *complete, self.buffer = self.buffer.split(b"\n")
        self.lines.extend(c.decode() + "\n" for c in complete)
        return True

    def read_now(self) -> None:
        """Every line already written, without blocking."""
        while True:
            before = len(self.lines), len(self.buffer)
            if not self._fill(0) or (len(self.lines), len(self.buffer)) == before:
                return

    def read_handshake(self) -> str:
        """The address the first handshake line names, reading (and
        skipping non-handshake lines) until it arrives or DEADLINE passes."""
        end = time.monotonic() + DEADLINE
        while True:
            for i, line in enumerate(self.lines):
                address = parse_handshake(line)
                if address is not None:
                    self.handshake_at = i
                    self.address = address
                    return address
            left = end - time.monotonic()
            if left <= 0:
                pytest.fail(f"no handshake within {DEADLINE}s; stdout {self.lines!r}")
            if not self._fill(left):
                self.finish()
                pytest.fail(f"stdout closed before a handshake; stderr: {self.stderr}")

    def finish(self) -> None:
        """Wait for the child to exit, keeping its exit code, the rest of
        its stdout and its stderr."""
        out, err = self.proc.communicate(timeout=DEADLINE)
        self.buffer += out
        *complete, rest = self.buffer.split(b"\n")
        self.lines.extend(c.decode() + "\n" for c in complete)
        if rest:
            self.lines.append(rest.decode())
        self.buffer = b""
        self.exit_code = self.proc.returncode
        self.stderr = err.decode()


@pytest.fixture
def world():
    # A short directory: a Unix socket path holds at most 107 bytes, more
    # than pytest's own tmp_path may leave.
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="cc-"))
    w = _World(tmp=tmp)
    yield w
    for ch in w.channels:
        ch.close()
    if w.proc is not None and w.proc.poll() is None:
        w.proc.kill()
        w.proc.communicate()
    shutil.rmtree(tmp, ignore_errors=True)


def _launch(world: _World) -> None:
    env = dict(os.environ)
    env.pop(GATE_ENV, None)
    env[SOCKET_ENV] = world.socket_path
    if world.held:
        world.gate.mkdir()
        env[GATE_ENV] = str(world.gate)
    world.proc = subprocess.Popen(
        [sys.executable, "-m", "tests.client.child_order"],
        cwd=REPO,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if world.held:
        # The fixture's sync point (tests/client/child_order.py): the child
        # is bound and has found its health not yet SERVING.
        end = time.monotonic() + DEADLINE
        while not (world.gate / "held").exists():
            if world.proc.poll() is not None or time.monotonic() > end:
                world.finish()
                pytest.fail(
                    f"the held child never looked at its health: {world.stderr}"
                )
            time.sleep(0.01)


def _health(world: _World) -> int:
    stub = health_pb2_grpc.HealthStub(world.channel())
    return stub.Check(health_pb2.HealthCheckRequest(service=""), timeout=5).status


# --- Given -----------------------------------------------------------------


@given(parsers.parse('a child serving an aggregate component for domain "{domain}"'))
def _child(world: _World, domain: str) -> None:
    assert domain == "order"


@given(
    parsers.parse(
        'a child serving an aggregate component for domain "{domain}", its readiness held'
    )
)
def _child_held(world: _World, domain: str) -> None:
    assert domain == "order"
    world.held = True


@given("a parent that names a socket path in ANGZARR_SOCKET")
def _names_socket(world: _World) -> None:
    world.socket_path = str(world.tmp / "child.sock")


@given(
    parsers.parse(
        'a started child serving an aggregate component for domain "{domain}"'
    )
)
def _started(world: _World, domain: str) -> None:
    assert domain == "order"
    world.socket_path = str(world.tmp / "child.sock")
    _launch(world)
    world.read_handshake()


# --- When ------------------------------------------------------------------


@when("the parent launches the child")
def _launches(world: _World) -> None:
    _launch(world)


@when("the child's readiness is released")
def _release(world: _World) -> None:
    (world.gate / "release").touch()


@when(
    parsers.parse(
        'a ContextualCommand for domain "{domain}" is sent to '
        "CommandHandlerService.Handle at its socket"
    )
)
def _handle(world: _World, domain: str) -> None:
    cc = types_pb2.ContextualCommand()
    cc.command.cover.domain = domain
    cc.command.cover.root.value = bytes(16)
    cc.command.page.command.payload.CopyFrom(pack(wrappers_pb2.StringValue(value="x")))
    stub = command_handler_pb2_grpc.CommandHandlerServiceStub(world.channel())
    response = stub.Handle(cc, timeout=10)
    assert len(response.events.pages) == 1


@when("the parent closes the child's stdin")
def _close_stdin(world: _World) -> None:
    world.proc.stdin.close()
    world.finish()


@when("the parent sends the child SIGTERM")
def _sigterm(world: _World) -> None:
    world.proc.send_signal(signal.SIGTERM)
    world.finish()


@when(parsers.re(r'the stdout line "(?P<line>[^"]*)" is read'))
def _read_line(world: _World, line: str) -> None:
    try:
        world.parsed = parse_handshake(line)
    except ConfigurationError as e:
        world.error = e


# --- Then ------------------------------------------------------------------


@then("the first handshake line on the child's stdout names that socket path")
def _names_that_path(world: _World) -> None:
    assert world.read_handshake() == f"unix:{world.socket_path}"


@then("ComponentService.Describe is served at that socket")
def _describe(world: _World) -> None:
    stub = component_pb2_grpc.ComponentServiceStub(world.channel())
    descriptor = stub.Describe(component_pb2.DescribeRequest(), timeout=10)
    assert descriptor.name == "order-component"
    assert descriptor.kind == types_pb2.COMPONENT_KIND_AGGREGATE


@then(
    parsers.re(
        r"the gRPC health service at that socket reports "
        r"(?P<status>SERVING|NOT_SERVING) for the overall server"
    )
)
def _health_is(world: _World, status: str) -> None:
    assert _health(world) == health_pb2.HealthCheckResponse.ServingStatus.Value(status)


@then("the child has written no handshake line on stdout")
def _no_handshake_yet(world: _World) -> None:
    world.read_now()
    pending = [world.buffer.decode()] if world.buffer else []
    assert not [line for line in world.lines + pending if _is_handshake_line(line)]


@then("no line on the child's stdout after its handshake is a handshake line")
def _only_one(world: _World) -> None:
    assert world.exit_code == 0, world.stderr
    after = world.lines[world.handshake_at + 1 :]
    assert not [line for line in after if _is_handshake_line(line)], after


@then(parsers.parse("the child exits with status {code:d}"))
def _exits(world: _World, code: int) -> None:
    assert world.exit_code == code, world.stderr


@then("the socket file the handshake named no longer exists")
def _socket_gone(world: _World) -> None:
    assert world.address == f"unix:{world.socket_path}"
    assert not os.path.exists(world.socket_path)


@then(parsers.re(r'it is a handshake naming socket path "(?P<path>[^"]*)"'))
def _is_handshake(world: _World, path: str) -> None:
    assert world.error is None, world.error
    assert world.parsed == f"unix:{path}"


@then("it is not a handshake")
def _not_handshake(world: _World) -> None:
    assert world.error is None, world.error
    assert world.parsed is None


@then(
    parsers.re(
        r'reading it fails with reason (?P<reason>[A-Z0-9_]+) and line "(?P<line>[^"]*)"'
    )
)
def _malformed(world: _World, reason: str, line: str) -> None:
    assert isinstance(world.error, ConfigurationError), world.parsed
    assert world.error.code == reason
    assert world.error.details == {"line": line}
