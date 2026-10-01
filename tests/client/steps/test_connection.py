"""Step defs for parity/client/connection.feature.

Every connection is a real gRPC connection made by the library's client
constructors (``connect`` / ``from_env`` / ``from_channel``) to an
in-process coordinator backend (:mod:`._grpc_backend`). Scenario
endpoints name well-known places; the world maps them onto the test
environment:

- port ``1310`` (the coordinator default) is the scenario's TCP backend;
- port ``59999`` is a localhost port with nothing listening;
- a Unix socket path named by "a Unix socket at ..." is a backend bound
  to a private temporary path standing in for it; any other socket path
  maps into an empty private directory.

grpc-python channels connect lazily, so "connecting" is the constructor
plus the first RPC over the new channel: that RPC is what opens the
transport, and transport failures surface from it as the client's
:class:`GRPCError`.
"""

from __future__ import annotations

import inspect
import os
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

import grpc
import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from angzarr_client._pb import (
    CommandBook,
    CommandPage,
    CommandRequest,
    Cover,
    EventBook,
    EventPage,
    EventQueryServiceStub,
    Query,
    SpeculateProjectorRequest,
)
from angzarr_client.client import (
    CommandHandlerClient,
    DomainClient,
    QueryClient,
    SpeculativeClient,
)
from angzarr_client.errors import GRPCError

from ._grpc_backend import (
    HTTP2_PREFACE,
    SPECULATIVE_PROJECTOR,
    GrpcBackend,
    TlsProbe,
    is_tls_client_hello,
    unused_tcp_port,
)

scenarios("parity/client/connection.feature")

RPC_TIMEOUT_S = 5.0
DOMAIN = "orders"


@dataclass
class _World:
    monkeypatch: pytest.MonkeyPatch
    backend: Optional[GrpcBackend] = None
    stopped: list[GrpcBackend] = field(default_factory=list)
    closed_port: Optional[int] = None
    sockets: dict[str, str] = field(default_factory=dict)
    temp_dirs: list[str] = field(default_factory=list)
    probes: list[TlsProbe] = field(default_factory=list)
    channels: list[grpc.Channel] = field(default_factory=list)
    query: Optional[QueryClient] = None
    command: Optional[CommandHandlerClient] = None
    speculative: Optional[SpeculativeClient] = None
    domain: Optional[DomainClient] = None
    channel: Optional[grpc.Channel] = None
    channel_peer: Optional[str] = None
    connect_book: Optional[EventBook] = None
    error: Optional[Exception] = None
    operation_error: Optional[Exception] = None
    failed_client: Optional[QueryClient] = None
    first_port: Optional[int] = None

    # -- environment mapping ------------------------------------------------

    def tcp_backend(self) -> GrpcBackend:
        if self.backend is None:
            self.backend = GrpcBackend.start_tcp()
        return self.backend

    def private_dir(self) -> str:
        d = tempfile.mkdtemp(prefix="angzarr-conn-")
        self.temp_dirs.append(d)
        return d

    def resolve(self, endpoint: str) -> str:
        for scenario_path, real in self.sockets.items():
            if endpoint.endswith(scenario_path):
                return endpoint[: -len(scenario_path)] + real
        if ":1310" in endpoint:
            port = self.tcp_backend().port
            assert port is not None, "scenario backend is not a TCP backend"
            return endpoint.replace(":1310", f":{port}")
        if ":59999" in endpoint:
            if self.closed_port is None:
                self.closed_port = unused_tcp_port()
            return endpoint.replace(":59999", f":{self.closed_port}")
        if endpoint.endswith(".sock"):
            path = endpoint[endpoint.index("/") :].lstrip("/")
            real = os.path.join(self.private_dir(), os.path.basename(path))
            prefix = endpoint[: endpoint.index("/")]
            return f"{prefix}{real}" if prefix else real
        return endpoint

    # -- client helpers -----------------------------------------------------

    def connected_query(self) -> QueryClient:
        if self.error is not None:
            detail = (
                f"{self.error.grpc_code}: {self.error.grpc_details}"
                if isinstance(self.error, GRPCError)
                else repr(self.error)
            )
            raise AssertionError(f"connection failed: {detail}")
        assert self.query is not None, "no QueryClient connected"
        return self.query

    def connect_query(self, scenario_endpoint: str) -> None:
        """Construct via QueryClient.connect and make the first RPC."""
        endpoint = self.resolve(scenario_endpoint)
        self.error = None
        try:
            self.query = QueryClient.connect(endpoint)
            self.connect_book = query_ok(self.query)
        except Exception as e:  # noqa: BLE001 — the step records any failure
            self.error = e

    def err(self) -> GRPCError:
        assert self.error is not None, "connection unexpectedly succeeded"
        assert isinstance(self.error, GRPCError), f"got {self.error!r}"
        return self.error

    def close(self) -> None:
        for c in (
            self.query,
            self.command,
            self.speculative,
            self.domain,
            self.failed_client,
        ):
            if c is not None and getattr(c, "_owns_channel", False):
                c.close()
        for ch in self.channels:
            ch.close()
        for b in [self.backend, *self.stopped]:
            if b is not None:
                b.stop()
        for p in self.probes:
            p.close()
        for d in self.temp_dirs:
            shutil.rmtree(d, ignore_errors=True)


def _query_request() -> Query:
    return Query(cover=Cover(domain=DOMAIN))


def query_ok(client: QueryClient) -> EventBook:
    book = client.get_event_book(_query_request(), timeout=RPC_TIMEOUT_S)
    assert book.cover.domain == DOMAIN, "response did not come from the backend"
    return book


def command_ok(client: CommandHandlerClient) -> None:
    request = CommandRequest(
        command=CommandBook(
            cover=Cover(domain=DOMAIN, correlation_id=str(uuid.uuid4())),
            pages=[CommandPage()],
        )
    )
    resp = client.handle_command(request, timeout=RPC_TIMEOUT_S)
    assert resp.events.cover.domain == DOMAIN
    assert len(resp.events.pages) == 1


@pytest.fixture
def state(monkeypatch: pytest.MonkeyPatch):
    world = _World(monkeypatch=monkeypatch)
    yield world
    world.close()


# ---------------------------------------------------------------------------
# TCP
# ---------------------------------------------------------------------------


@when(parsers.parse('I connect to "{endpoint}"'))
def _when_connect_to(state: _World, endpoint: str) -> None:
    state.connect_query(endpoint)


@then("the connection should succeed")
def _then_connection_succeeds(state: _World) -> None:
    state.connected_query()
    assert state.connect_book is not None
    assert list(state.connect_book.pages) == []
    assert state.backend is not None
    assert state.backend.methods() == ["GetEventBook"]


@then("the client should be ready for operations")
def _then_client_ready(state: _World) -> None:
    before = len(state.tcp_backend().rpcs())
    query_ok(state.connected_query())
    methods = state.tcp_backend().methods()
    assert len(methods) == before + 1
    assert methods[-1] == "GetEventBook"


@then("the scheme should be treated as insecure")
def _then_scheme_insecure(state: _World) -> None:
    """The backend speaks plaintext HTTP/2 only; an RPC completing over
    the ``http://`` connection proves no TLS was negotiated."""
    query_ok(state.connected_query())
    assert state.tcp_backend().methods() == ["GetEventBook", "GetEventBook"]


@then("the connection should use TLS")
def _then_connection_uses_tls(state: _World) -> None:
    """A client given ``https://`` must open its transport with a TLS
    ClientHello. A raw listener records the first bytes the client sends."""
    probe = TlsProbe()
    state.probes.append(probe)
    client = QueryClient.connect(f"https://localhost:{probe.port}")
    with pytest.raises(GRPCError):
        client.get_event_book(_query_request(), timeout=2.0)
    client.close()
    received = probe.first_bytes()
    assert received, (
        "https endpoint never opened a TCP connection "
        f"(connect error was: {getattr(state.error, 'grpc_details', state.error)!r})"
    )
    assert is_tls_client_hello(
        received[0]
    ), f"https endpoint did not start a TLS handshake; first bytes {received[0]!r}"
    assert not received[0].startswith(HTTP2_PREFACE)


@then("the connection should fail")
def _then_connection_fails(state: _World) -> None:
    state.err()


@then("the error should indicate DNS or connection failure")
def _then_error_dns_failure(state: _World) -> None:
    err = state.err()
    assert err.is_connection_error(), f"got {err!r}"
    assert err.grpc_code == grpc.StatusCode.UNAVAILABLE
    details = err.grpc_details.lower()
    assert "resolving" in details or "domain name not found" in details, details


@then("the error should indicate connection refused")
def _then_error_connection_refused(state: _World) -> None:
    err = state.err()
    assert err.is_connection_error(), f"got {err!r}"
    assert "connection refused" in err.grpc_details.lower(), err.grpc_details


# ---------------------------------------------------------------------------
# Unix domain sockets
# ---------------------------------------------------------------------------


@given(parsers.parse('a Unix socket at "{path}"'))
def _given_unix_socket(state: _World, path: str) -> None:
    real = os.path.join(state.private_dir(), os.path.basename(path))
    state.backend = GrpcBackend.start_uds(real)
    assert os.path.exists(real), "backend did not create its socket"
    state.sockets[path] = real


@then("the client should use UDS transport")
def _then_client_uses_uds(state: _World) -> None:
    query_ok(state.connected_query())
    backend = state.backend
    assert (
        backend is not None and backend.port is None
    ), "backend must listen only on a Unix socket"
    rpcs = backend.rpcs()
    assert [r.method for r in rpcs] == ["GetEventBook", "GetEventBook"]
    assert all(r.peer.startswith("unix:") for r in rpcs), [r.peer for r in rpcs]


@then("the error should indicate socket not found")
def _then_error_socket_not_found(state: _World) -> None:
    err = state.err()
    assert err.is_connection_error(), f"got {err!r}"
    assert "no such file" in err.grpc_details.lower(), err.grpc_details


# ---------------------------------------------------------------------------
# Environment variables
# ---------------------------------------------------------------------------


@given(parsers.re(r'environment variable "(?P<name>[^"]+)" set to "(?P<value>[^"]*)"'))
def _given_env_var_set(state: _World, name: str, value: str) -> None:
    state.monkeypatch.setenv(name, state.resolve(value) if value else value)


@given(parsers.parse('environment variable "{name}" is not set'))
def _given_env_var_not_set(state: _World, name: str) -> None:
    state.monkeypatch.delenv(name, raising=False)


@when(parsers.parse('I call from_env("{var_name}", "{default}")'))
def _when_call_from_env(state: _World, var_name: str, default: str) -> None:
    state.error = None
    try:
        state.query = QueryClient.from_env(var_name, state.resolve(default))
        state.connect_book = query_ok(state.query)
    except Exception as e:  # noqa: BLE001
        state.error = e


@then(parsers.parse('the connection should use "{expected}"'))
def _then_connection_uses_endpoint(state: _World, expected: str) -> None:
    """Only the backend standing in for ``expected`` can answer; the
    competing endpoint (``default:9999`` or an unset/empty variable)
    does not resolve to it."""
    assert expected.endswith(":1310"), "scenario names the backend port"
    state.connected_query()
    assert state.tcp_backend().methods() == ["GetEventBook"]


# ---------------------------------------------------------------------------
# Channel reuse
# ---------------------------------------------------------------------------


@given("an existing gRPC channel")
def _given_existing_channel(state: _World) -> None:
    """A caller-owned channel with a private subchannel pool, so any other
    channel to the same target opens its own connection. One raw RPC over
    it records the peer that identifies its connection."""
    port = state.tcp_backend().port
    channel = grpc.insecure_channel(
        f"localhost:{port}", options=[("grpc.use_local_subchannel_pool", 1)]
    )
    state.channels.append(channel)
    EventQueryServiceStub(channel).GetEventBook(_query_request(), timeout=RPC_TIMEOUT_S)
    rpcs = state.tcp_backend().rpcs()
    assert [r.method for r in rpcs] == ["GetEventBook"]
    state.channel = channel
    state.channel_peer = rpcs[0].peer


@when("I call from_channel(channel)")
def _when_call_from_channel(state: _World) -> None:
    assert state.channel is not None
    state.query = QueryClient.from_channel(state.channel)


@then("the client should reuse that channel")
def _then_client_reuses_channel(state: _World) -> None:
    query_ok(state.connected_query())
    rpcs = state.tcp_backend().rpcs()
    assert [r.method for r in rpcs] == ["GetEventBook", "GetEventBook"]
    assert rpcs[-1].peer == state.channel_peer


@then("no new connection should be created")
def _then_no_new_connection(state: _World) -> None:
    query_ok(state.connected_query())
    assert state.tcp_backend().peers() == {state.channel_peer}
    state.connected_query().close()
    # The caller still owns the channel: closing the client leaves it usable.
    assert state.channel is not None
    EventQueryServiceStub(state.channel).GetEventBook(
        _query_request(), timeout=RPC_TIMEOUT_S
    )
    assert state.tcp_backend().peers() == {state.channel_peer}


@when("I create QueryClient from the channel")
def _when_create_query_client_from_channel(state: _World) -> None:
    assert state.channel is not None
    state.query = QueryClient.from_channel(state.channel)


@when("I create CommandHandlerClient from the same channel")
def _when_create_command_handler_client_from_channel(state: _World) -> None:
    assert state.channel is not None
    state.command = CommandHandlerClient.from_channel(state.channel)


@then("both clients should share the connection")
def _then_clients_share_connection(state: _World) -> None:
    assert state.command is not None
    query_ok(state.connected_query())
    command_ok(state.command)
    rpcs = state.tcp_backend().rpcs()
    assert [r.method for r in rpcs] == ["GetEventBook", "GetEventBook", "HandleCommand"]
    assert rpcs[1].peer == rpcs[2].peer == state.channel_peer


@then("the connection should only be established once")
def _then_connection_established_once(state: _World) -> None:
    assert state.tcp_backend().peers() == {state.channel_peer}


# ---------------------------------------------------------------------------
# Client types
# ---------------------------------------------------------------------------


@when(parsers.parse('I create a QueryClient connected to "{endpoint}"'))
def _when_create_query_client(state: _World, endpoint: str) -> None:
    state.query = QueryClient.connect(state.resolve(endpoint))


@then("the client should be able to query events")
def _then_client_can_query(state: _World) -> None:
    book = query_ok(state.connected_query())
    assert list(book.pages) == []
    assert state.tcp_backend().methods() == ["GetEventBook"]


@when(parsers.parse('I create a CommandHandlerClient connected to "{endpoint}"'))
def _when_create_command_handler_client(state: _World, endpoint: str) -> None:
    state.command = CommandHandlerClient.connect(state.resolve(endpoint))


@then("the client should be able to execute commands")
def _then_client_can_execute(state: _World) -> None:
    assert state.command is not None
    command_ok(state.command)
    assert state.tcp_backend().methods() == ["HandleCommand"]


@when(parsers.parse('I create a SpeculativeClient connected to "{endpoint}"'))
def _when_create_speculative_client(state: _World, endpoint: str) -> None:
    state.speculative = SpeculativeClient.connect(state.resolve(endpoint))


@then("the client should be able to perform speculative operations")
def _then_client_can_speculate(state: _World) -> None:
    assert state.speculative is not None
    events = EventBook(cover=Cover(domain=DOMAIN), pages=[EventPage(), EventPage()])
    projection = state.speculative.projector(
        SpeculateProjectorRequest(events=events), timeout=RPC_TIMEOUT_S
    )
    assert projection.projector == SPECULATIVE_PROJECTOR
    assert projection.cover.domain == DOMAIN
    assert projection.sequence == 2
    assert state.tcp_backend().methods() == ["HandleSpeculative"]


@when(parsers.parse('I create a DomainClient connected to "{endpoint}"'))
def _when_create_domain_client(state: _World, endpoint: str) -> None:
    state.domain = DomainClient.connect(state.resolve(endpoint))


@then("the client should have aggregate and query sub-clients")
def _then_client_has_sub_clients(state: _World) -> None:
    assert state.domain is not None
    command_ok(state.domain.command_handler)
    query_ok(state.domain.query)


@then("both should share the same connection")
def _then_both_share_connection(state: _World) -> None:
    assert state.domain is not None
    rpcs = state.tcp_backend().rpcs()
    assert [r.method for r in rpcs] == ["HandleCommand", "GetEventBook"]
    assert len({r.peer for r in rpcs}) == 1, [r.peer for r in rpcs]
    # One channel, owned by the DomainClient, underlies both sub-clients.
    assert (
        state.domain.command_handler._channel is state.domain._channel
    )  # noqa: SLF001
    assert state.domain.query._channel is state.domain._channel  # noqa: SLF001


@when(parsers.parse('I create a Client connected to "{endpoint}"'))
def _when_create_full_client(state: _World, endpoint: str) -> None:
    _when_create_domain_client(state, endpoint)


@then("the client should have aggregate, query, and speculative sub-clients")
def _then_client_has_all_sub_clients(state: _World) -> None:
    _then_client_has_sub_clients(state)
    assert state.domain is not None
    projection = state.domain.speculative.projector(
        SpeculateProjectorRequest(events=EventBook(cover=Cover(domain=DOMAIN))),
        timeout=RPC_TIMEOUT_S,
    )
    assert projection.projector == SPECULATIVE_PROJECTOR


# ---------------------------------------------------------------------------
# Connection options
# ---------------------------------------------------------------------------


def _connect_params() -> list[str]:
    return list(inspect.signature(QueryClient.connect).parameters)


@when(parsers.parse("I connect with timeout of {seconds:d} seconds"))
def _when_connect_with_timeout(state: _World, seconds: int) -> None:
    raise AssertionError(
        "angzarr_client has no connect-timeout option: QueryClient.connect / "
        f"CommandHandlerClient.connect take {_connect_params()} and _create_channel "
        "passes no channel options; deadlines exist only per RPC "
        f"(requested {seconds}s)"
    )


@then("the connection should respect the timeout")
def _then_connection_respects_timeout(state: _World) -> None:
    raise AssertionError("no connect-timeout option to observe")


@then("slow connections should fail after timeout")
def _then_slow_connections_fail(state: _World) -> None:
    raise AssertionError("no connect-timeout option to observe")


@when("I connect with keep-alive enabled")
def _when_connect_with_keepalive(state: _World) -> None:
    state.connect_query("localhost:1310")


@then("the connection should send keep-alive probes")
def _then_connection_sends_keepalive(state: _World) -> None:
    raise AssertionError(
        "angzarr_client has no keep-alive option: connect takes "
        f"{_connect_params()} and _create_channel sets no grpc.keepalive_* channel "
        "options, so no HTTP/2 keep-alive PINGs are configured"
    )


@then("idle connections should remain open")
def _then_idle_connections_remain(state: _World) -> None:
    query_ok(state.connected_query())
    time.sleep(0.5)
    query_ok(state.connected_query())
    assert len(state.tcp_backend().peers()) == 1


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


@then("the error should indicate invalid format")
def _then_error_invalid_format(state: _World) -> None:
    err = state.err()
    assert err.is_connection_error(), f"got {err!r}"
    assert "misformatted" in err.grpc_details.lower(), err.grpc_details


@given("an established connection")
def _given_established_connection(state: _World) -> None:
    state.connect_query("localhost:1310")
    state.connected_query()
    assert state.tcp_backend().methods() == ["GetEventBook"]


@when("the server disconnects")
def _when_server_disconnects(state: _World) -> None:
    backend = state.tcp_backend()
    backend.stop()
    state.stopped.append(backend)
    state.backend = None


@when("I attempt an operation")
def _when_attempt_operation(state: _World) -> None:
    try:
        query_ok(state.connected_query())
    except Exception as e:  # noqa: BLE001
        state.operation_error = e


@then("the operation should fail")
def _then_operation_fails(state: _World) -> None:
    assert state.operation_error is not None, "operation succeeded"


@then("the error should indicate connection lost")
def _then_error_connection_lost(state: _World) -> None:
    err = state.operation_error
    assert isinstance(err, GRPCError), f"got {err!r}"
    assert err.is_connection_error(), f"got {err!r}"
    assert err.grpc_code == grpc.StatusCode.UNAVAILABLE


@given("a connection that failed")
def _given_connection_failed(state: _World) -> None:
    backend = state.tcp_backend()
    state.first_port = backend.port
    state.connect_query("localhost:1310")
    client = state.connected_query()
    backend.stop()
    state.stopped.append(backend)
    state.backend = None
    with pytest.raises(GRPCError) as excinfo:
        query_ok(client)
    assert excinfo.value.is_connection_error()
    state.failed_client = client
    state.query = None


@when("I create a new client with the same endpoint")
def _when_create_new_client(state: _World) -> None:
    assert state.first_port is not None
    state.backend = GrpcBackend.start_tcp(state.first_port)
    state.error = None
    try:
        state.query = QueryClient.connect(f"localhost:{state.first_port}")
    except Exception as e:  # noqa: BLE001
        state.error = e


@then("the new connection should be independent")
def _then_new_connection_independent(state: _World) -> None:
    client = state.connected_query()
    assert state.failed_client is not None
    assert client._channel is not state.failed_client._channel  # noqa: SLF001
    state.failed_client.close()
    # Closing the failed client does not affect the new one.
    query_ok(client)
    assert len(state.tcp_backend().peers()) == 1


@then("the new connection should succeed if server is available")
def _then_new_connection_succeeds(state: _World) -> None:
    book = query_ok(state.connected_query())
    assert list(book.pages) == []
    methods = state.tcp_backend().methods()
    assert methods and all(m == "GetEventBook" for m in methods), methods
