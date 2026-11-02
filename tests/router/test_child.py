"""The child-process contract (angzarr_client.child): a parent launches the
counter fixture as a child (tests/router/child_counter.py), reads the
handshake line on its stdout, and calls ComponentService.Describe and
CommandHandlerService.Handle over the Unix socket it names."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

import grpc
import pytest

from angzarr_client import child as child_module
from angzarr_client.child import (
    HANDSHAKE_PREFIX,
    SOCKET_ENV,
    handshake_line,
    parse_handshake,
)
from angzarr_client.host import ConfigurationError
from angzarr_client.proto.io.angzarr.v1 import (
    command_handler_pb2_grpc,
    component_pb2,
    component_pb2_grpc,
    types_pb2,
)

from . import builders
from .builders import TEST_RULES_VERSION

REPO = Path(__file__).resolve().parents[2]
STARTUP_TIMEOUT = 30


# --- the line itself (docs/design/child-protocol.md, section 2) -------------------------


def test_the_handshake_line_names_the_format_version_and_the_address():
    # The line's first word and its format version, as angzarr-embedded's
    # handshake module names them.
    assert HANDSHAKE_PREFIX == "angzarr-handshake"
    assert child_module.PROTOCOL_VERSION == "v1"
    assert SOCKET_ENV == "ANGZARR_SOCKET"
    assert (
        handshake_line("unix:/run/app/player.sock")
        == "angzarr-handshake v1 unix:/run/app/player.sock\n"
    )


def test_a_handshake_line_parses_back_to_its_address():
    line = handshake_line("unix:/run/app/player.sock")
    assert parse_handshake(line) == "unix:/run/app/player.sock"
    assert parse_handshake(line.rstrip("\n")) == "unix:/run/app/player.sock"


def test_a_crlf_line_ending_is_not_part_of_the_path():
    line = "angzarr-handshake v1 unix:/run/app/player.sock\r\n"
    assert parse_handshake(line) == "unix:/run/app/player.sock"


def test_the_path_is_the_rest_of_the_line_spaces_included():
    line = "angzarr-handshake v1 unix:/run/my app/player.sock\n"
    assert parse_handshake(line) == "unix:/run/my app/player.sock"


@pytest.mark.parametrize(
    "line",
    [
        "",
        "\n",
        "hello\n",
        "angzarr-handshaker v1 unix:/run/app/player.sock\n",
        "angzarr-handshake\tv1 unix:/run/app/player.sock\n",
    ],
)
def test_a_line_whose_first_word_is_not_the_prefix_is_not_a_handshake(line):
    assert parse_handshake(line) is None


@pytest.mark.parametrize(
    "line",
    [
        "angzarr-handshake\n",
        "angzarr-handshake \n",
        "angzarr-handshake v1\n",
        "angzarr-handshake v2 unix:/run/app/player.sock\n",
        "angzarr-handshake v1 127.0.0.1:5000\n",
        "angzarr-handshake v1 unix:relative.sock\n",
        "angzarr-handshake v1 unix:\r\n",
        "angzarr-handshake  v1 unix:/run/app/player.sock\n",
        "angzarr-handshake v1 unix:/run/app\nplayer.sock\n",
    ],
)
def test_a_line_starting_with_the_prefix_and_not_a_v1_handshake_is_malformed(line):
    with pytest.raises(ConfigurationError) as info:
        parse_handshake(line)
    assert info.value.code == "HOST_REASON_HANDSHAKE_MALFORMED"
    # The line as read, its line ending removed.
    assert info.value.details == {"line": line.removesuffix("\n").removesuffix("\r")}


# --- the child process -------------------------------------------------------------------


class Child:
    """A child fixture (``module`` under tests/router, given ``argv``; the
    counter by default) running as a child process, launched as a parent
    would: stdin and stdout piped, the socket path (optionally) in the
    environment."""

    def __init__(
        self, socket_path: str | None, *argv: str, module: str = "child_counter"
    ) -> None:
        env = dict(os.environ)
        env.pop(SOCKET_ENV, None)
        if socket_path is not None:
            env[SOCKET_ENV] = socket_path
        self.proc = subprocess.Popen(
            [sys.executable, "-m", f"tests.router.{module}", *argv],
            cwd=REPO,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.line = self._read_line()
        self.address = parse_handshake(self.line)

    def _read_line(self) -> str:
        """The first line of stdout, failing the test (with the child's
        stderr) if the child exits or stalls first."""
        import selectors

        sel = selectors.DefaultSelector()
        sel.register(self.proc.stdout, selectors.EVENT_READ)
        if not sel.select(timeout=STARTUP_TIMEOUT):
            self.proc.kill()
            pytest.fail(f"no handshake within {STARTUP_TIMEOUT}s")
        line = self.proc.stdout.readline().decode()
        if not line:
            _, err = self.proc.communicate(timeout=10)
            pytest.fail(f"child exited before its handshake: {err.decode()}")
        return line

    def finish(self, how: str = "stdin") -> tuple[int, str, str]:
        """End the child (closing stdin, or by SIGTERM) and return its exit
        code, the rest of its stdout and its stderr."""
        if how == "sigterm":
            self.proc.send_signal(signal.SIGTERM)
        out, err = self.proc.communicate(timeout=30)
        return self.proc.returncode, out.decode(), err.decode()

    def kill(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.communicate()


@pytest.fixture
def socket_path():
    with tempfile.TemporaryDirectory() as tmp:
        yield os.path.join(tmp, "child.sock")


@pytest.fixture
def child(socket_path):
    c = Child(socket_path)
    yield c
    c.kill()


def test_the_child_announces_the_socket_the_parent_named(child, socket_path):
    assert child.line == f"angzarr-handshake v1 unix:{socket_path}\n"
    assert os.path.exists(socket_path)


def test_a_relative_socket_path_is_announced_absolute():
    c = Child("child-relative.sock")
    try:
        # The child runs in the repository root: the path is made absolute
        # against the child's own working directory.
        assert c.address == f"unix:{REPO / 'child-relative.sock'}"
    finally:
        c.finish()
    assert not (REPO / "child-relative.sock").exists()


def test_without_a_named_socket_the_child_picks_one_in_the_temp_dir():
    c = Child(None)
    try:
        path = c.address.removeprefix("unix:")
        assert os.path.isabs(path)
        assert os.path.dirname(path) == tempfile.gettempdir()
        assert os.path.exists(path)
    finally:
        c.finish()
    assert not os.path.exists(path)


def test_the_child_serves_describe_over_the_socket(child):
    with grpc.insecure_channel(child.address) as channel:
        stub = component_pb2_grpc.ComponentServiceStub(channel)
        descriptor = stub.Describe(component_pb2.DescribeRequest(), timeout=10)
    assert descriptor.name == "CounterAggregate"
    assert descriptor.kind == types_pb2.COMPONENT_KIND_AGGREGATE
    assert descriptor.rules_version == TEST_RULES_VERSION


def test_the_child_serves_handle_over_the_socket(child):
    with grpc.insecure_channel(child.address) as channel:
        stub = command_handler_pb2_grpc.CommandHandlerServiceStub(channel)
        response = stub.Handle(builders.increase_command(2), timeout=10)
    assert [
        builders.fq_from_url(p.payload.type_url) for p in response.events.pages
    ] == [
        builders.FQ_INCREASED,
        builders.FQ_INCREASED,
    ]
    assert response.rules_version == TEST_RULES_VERSION


def test_closing_stdin_stops_the_child_and_removes_its_socket(child, socket_path):
    code, rest, err = child.finish("stdin")
    assert code == 0, err
    assert rest == ""
    assert not os.path.exists(socket_path)


def test_sigterm_stops_the_child_and_removes_its_socket(child, socket_path):
    code, rest, err = child.finish("sigterm")
    assert code == 0, err
    assert rest == ""
    assert not os.path.exists(socket_path)


def test_the_handshake_is_the_only_thing_on_stdout_and_logs_go_to_stderr(child):
    # A served call makes the child log; none of it may reach stdout.
    with grpc.insecure_channel(child.address) as channel:
        component_pb2_grpc.ComponentServiceStub(channel).Describe(
            component_pb2.DescribeRequest(), timeout=10
        )
    code, rest, err = child.finish("stdin")
    assert code == 0, err
    assert rest == ""
    assert '"event": "child_serving"' in err
    assert child.address in err


def test_a_child_that_cannot_bind_exits_without_a_handshake(tmp_path):
    # A socket path in a directory that does not exist cannot be bound.
    missing = tmp_path / "absent" / "child.sock"
    env = dict(os.environ, **{SOCKET_ENV: str(missing)})
    proc = subprocess.run(
        [sys.executable, "-m", "tests.router.child_counter"],
        cwd=REPO,
        env=env,
        stdin=subprocess.PIPE,
        capture_output=True,
        timeout=STARTUP_TIMEOUT,
    )
    assert proc.returncode != 0
    assert proc.stdout == b""


def test_a_child_given_a_socket_path_not_utf8_binds_nothing_and_exits(tmp_path):
    # No handshake could name the path (decision 14.62): the child refuses
    # it before it binds, under the malformed handshake's key.
    raw = os.fsencode(tmp_path) + b"/caf\xe9.sock"
    env = dict(os.environ, **{SOCKET_ENV: os.fsdecode(raw)})
    proc = subprocess.run(
        [sys.executable, "-m", "tests.router.child_counter"],
        cwd=REPO,
        env=env,
        stdin=subprocess.PIPE,
        capture_output=True,
        timeout=STARTUP_TIMEOUT,
    )
    assert proc.returncode != 0
    assert proc.stdout == b""
    assert not os.path.lexists(raw)
    assert b"HOST_REASON_HANDSHAKE_MALFORMED" in proc.stderr


@pytest.mark.parametrize(
    ("raw", "line"),
    [
        (
            b"angzarr-handshake v1 unix:/run/caf\xe9.sock\n",
            "angzarr-handshake v1 unix:/run/caf\\xe9.sock",
        ),
        (
            b"angzarr-handshake v1 unix:/run/a\\b\t\x7f~\xff.sock\r\n",
            "angzarr-handshake v1 unix:/run/a\\x5cb\\x09\\x7f~\\xff.sock",
        ),
        (
            b"angzarr-handshake v2 unix:/run/a\\b\n",
            "angzarr-handshake v2 unix:/run/a\\b",
        ),
    ],
)
def test_a_line_of_bytes_not_utf8_is_malformed_and_given_back_escaped(raw, line):
    with pytest.raises(ConfigurationError) as info:
        parse_handshake(raw)
    assert info.value.code == "HOST_REASON_HANDSHAKE_MALFORMED"
    assert info.value.details == {"line": line}


def test_a_line_of_bytes_is_read_as_its_utf8_text():
    assert parse_handshake(b"angzarr-handshake v1 unix:/run/caf\xc3\xa9.sock\r\n") == (
        "unix:/run/caf\u00e9.sock"
    )
    assert parse_handshake(b"caf\xe9 starting\n") is None
    assert parse_handshake(b"\n") is None


@pytest.mark.timeout(30)
def test_the_child_closes_stdout_after_the_handshake(child):
    # End of file on stdout while the child still serves: read() returns
    # only once every writer has closed it (a child holding it open hangs
    # here until the timeout fails the test).
    assert child.proc.stdout.read() == b""
    assert child.proc.poll() is None
    code, _, err = child.finish("stdin")
    assert code == 0, err
