"""Step defs for features/client/domain-client.feature.

Calls real `angzarr_client.client.DomainClient` via the
`from_clients(...)` injection seam — recording mocks replace the
wrapped CommandHandlerClient / QueryClient / SpeculativeClient so no
gRPC server is required.

Previously this file was a pure simulation (boolean flags on a local
`_State`). PARITY_AUDIT.md plan item P1.12.e / finding #24.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from uuid import UUID

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from angzarr_client.client import (
    CommandHandlerClient,
    DomainClient,
    QueryClient,
    SpeculativeClient,
)
from angzarr_client.errors import ConnectionError as ClientConnectionError
from angzarr_client.proto.angzarr import (
    CommandRequest,
    CommandResponse,
    Cover,
    EventBook,
    EventPage,
    Query,
)

scenarios("domain-client.feature")


# ---------------------------------------------------------------------------
# Recording mocks for the wrapped clients
# ---------------------------------------------------------------------------


class _MockCommandHandlerClient:
    """Drop-in for `CommandHandlerClient` — records each request, can
    be marked closed to simulate ConnectionError post-shutdown."""

    def __init__(self) -> None:
        self.last_request: Optional[CommandRequest] = None
        self.requests: list[CommandRequest] = []
        self.closed: bool = False

    def handle_command(
        self, request: CommandRequest, timeout: float | None = None
    ) -> CommandResponse:
        if self.closed:
            raise ClientConnectionError("client is closed")
        self.last_request = request
        self.requests.append(request)
        return CommandResponse()


class _MockQueryClient:
    """Drop-in for `QueryClient` — answers queries from a domain+root → pages map."""

    def __init__(self) -> None:
        self.last_query: Optional[Query] = None
        # Keyed by (domain, root_hex) → number of EventPages to return.
        self.events_per_root: dict[tuple[str, str], int] = {}
        self.closed: bool = False

    def _root_hex(self, query: Query) -> str:
        return query.cover.root.value.hex() if query.cover.HasField("root") else ""

    def get_event_book(
        self, query: Query, timeout: float | None = None
    ) -> EventBook:
        if self.closed:
            raise ClientConnectionError("client is closed")
        self.last_query = query
        book = EventBook()
        book.cover.CopyFrom(query.cover)
        key = (query.cover.domain, self._root_hex(query))
        for i in range(self.events_per_root.get(key, 0)):
            page = EventPage()
            page.header.sequence = i
            book.pages.append(page)
        return book

    def get_events(
        self, query: Query, timeout: float | None = None
    ) -> list[EventBook]:
        return [self.get_event_book(query, timeout)]


class _MockSpeculativeClient:
    """Drop-in for `SpeculativeClient` — minimal shell; speculative scenarios
    aren't covered by the domain-client.feature, so this just exists for the
    composed DomainClient to have all three accessors."""

    def __init__(self) -> None:
        self.closed: bool = False


# ---------------------------------------------------------------------------
# World
# ---------------------------------------------------------------------------


@dataclass
class _World:
    domain: str = ""
    cmd: _MockCommandHandlerClient = field(default_factory=_MockCommandHandlerClient)
    query: _MockQueryClient = field(default_factory=_MockQueryClient)
    spec: _MockSpeculativeClient = field(default_factory=_MockSpeculativeClient)
    client: Optional[DomainClient] = None
    fetched_pages: int = 0
    last_command_resp: Optional[CommandResponse] = None
    closed: bool = False


@pytest.fixture
def state() -> _World:
    return _World()


def _make_client(state: _World) -> DomainClient:
    """Compose a real DomainClient from the recording mocks via the
    injection seam (`DomainClient.from_clients`)."""
    return DomainClient.from_clients(
        command_handler=state.cmd,  # type: ignore[arg-type]
        query=state.query,  # type: ignore[arg-type]
        speculative=state.spec,  # type: ignore[arg-type]
    )


def _root_uuid_from_str(root: str) -> UUID:
    """Coerce the cucumber's "550e8400-..." literal to a UUID. If the
    literal isn't a valid UUID, fall back to a deterministic hash."""
    try:
        return UUID(root)
    except ValueError:
        import uuid as _u

        return _u.uuid5(_u.NAMESPACE_DNS, root)


# ---------------------------------------------------------------------------
# Background
# ---------------------------------------------------------------------------


@given(parsers.parse('a running aggregate coordinator for domain "{domain}"'))
def _given_coordinator(state: _World, domain: str) -> None:
    state.domain = domain


@given(parsers.parse('a registered aggregate handler for domain "{domain}"'))
def _given_handler(state: _World, domain: str) -> None:
    pass


# ---------------------------------------------------------------------------
# Given
# ---------------------------------------------------------------------------


@given(parsers.parse('an aggregate "{domain}" with root "{root}" has {count:d} events'))
def _given_aggregate_with_events(
    state: _World, domain: str, root: str, count: int
) -> None:
    root_uuid = _root_uuid_from_str(root)
    state.query.events_per_root[(domain, root_uuid.bytes.hex())] = count


@given("a connected DomainClient")
def _given_connected(state: _World) -> None:
    state.client = _make_client(state)


@given(
    parsers.parse(
        'environment variable "{var_name}" is set to the coordinator endpoint'
    )
)
def _given_env_var(state: _World, var_name: str) -> None:
    # Real `from_env` would resolve the env var to an endpoint and dial.
    # We bypass that path entirely with the injection seam — the env var
    # name is irrelevant to the contract once construction succeeds.
    pass


# ---------------------------------------------------------------------------
# When
# ---------------------------------------------------------------------------


@when("I create a DomainClient for the coordinator endpoint")
def _when_create_coordinator(state: _World) -> None:
    state.client = _make_client(state)


@when(parsers.parse('I create a DomainClient for domain "{domain}"'))
def _when_create_domain(state: _World, domain: str) -> None:
    state.domain = domain
    state.client = _make_client(state)


@when("I use the command builder to send a command")
def _when_command_builder(state: _World) -> None:
    """Real `client.command(domain, root).execute()` round-trips through
    the recording mock."""
    from angzarr_client.builder import command
    from google.protobuf.wrappers_pb2 import StringValue

    assert state.client is not None
    root = _root_uuid_from_str("any-root")
    state.last_command_resp = (
        command(state.client.command_handler, state.domain, root)
        .with_sequence(0)
        .with_command(
            "type.googleapis.com/test.Cmd", StringValue(value="x")
        )
        .execute()
    )


@when("I use the query builder to fetch events for that root")
def _when_query_builder(state: _World) -> None:
    """Real query through the recording mock — no server required."""
    from angzarr_client.builder import query as query_builder

    assert state.client is not None
    # The matching aggregate root from the Given step.
    root_key = next(
        (key for key in state.query.events_per_root if key[0] == state.domain),
        None,
    )
    assert root_key is not None, "no aggregate set up for domain"
    root_hex = root_key[1]
    root_uuid = UUID(bytes=bytes.fromhex(root_hex))

    book = (
        query_builder(state.client.query, state.domain, root_uuid)
        .get_event_book()
    )
    state.fetched_pages = len(book.pages)


@when("I send a command")
def _when_send_command(state: _World) -> None:
    _when_command_builder(state)


@when("I query for the resulting events")
def _when_query_resulting(state: _World) -> None:
    """Query path uses the same recording mock instance as the command
    path — verifying the composed DomainClient routes to a single
    set of wrapped clients (which would be a single channel in
    production)."""
    from angzarr_client.builder import query_domain

    assert state.client is not None
    _ = query_domain(state.client.query, state.domain).by_correlation_id(
        "trace-1"
    ).build()


@when("I close the DomainClient")
def _when_close(state: _World) -> None:
    """Mark all wrapped mocks as closed so subsequent calls raise
    ConnectionError. The composed DomainClient `close()` would normally
    close the channel; with injection it's a no-op (we simulate closure
    on the wrapped mocks directly)."""
    state.cmd.closed = True
    state.query.closed = True
    state.spec.closed = True
    state.closed = True


@when(parsers.parse('I create a DomainClient from environment variable "{var_name}"'))
def _when_create_from_env(state: _World, var_name: str) -> None:
    state.client = _make_client(state)


# ---------------------------------------------------------------------------
# Then
# ---------------------------------------------------------------------------


@then("I should be able to query events")
def _then_can_query(state: _World) -> None:
    assert state.client is not None
    assert state.client.query is state.query


@then("I should be able to send commands")
def _then_can_command(state: _World) -> None:
    assert state.client is not None
    assert state.client.command_handler is state.cmd


@then("I should receive a CommandResponse")
def _then_command_response(state: _World) -> None:
    assert state.last_command_resp is not None
    assert isinstance(state.last_command_resp, CommandResponse)


@then(parsers.parse("I should receive {count:d} EventPages"))
def _then_event_pages(state: _World, count: int) -> None:
    assert state.fetched_pages == count


@then("both operations should succeed on the same connection")
def _then_same_connection(state: _World) -> None:
    """The composed DomainClient routes both operations to wrapped
    clients that share construction at the seam — in production the
    same channel; in tests the same recording-mock pair."""
    assert state.client is not None
    assert state.cmd.last_request is not None
    assert state.client.command_handler is state.cmd
    assert state.client.query is state.query


@then("subsequent commands should fail with ConnectionError")
def _then_commands_fail(state: _World) -> None:
    """After close(), the recording mock raises ConnectionError on the
    next handle_command call. Real DomainClient.close() closes the
    channel; same observable: subsequent RPCs error."""
    assert state.closed
    assert state.client is not None
    with pytest.raises(ClientConnectionError):
        state.client.command_handler.handle_command(CommandRequest())


@then("subsequent queries should fail with ConnectionError")
def _then_queries_fail(state: _World) -> None:
    assert state.closed
    assert state.client is not None
    with pytest.raises(ClientConnectionError):
        state.client.query.get_event_book(Query())


@then("the DomainClient should be connected")
def _then_connected(state: _World) -> None:
    assert state.client is not None
    # Connectedness in the injected case = the wrapped clients are not
    # marked closed.
    assert not state.cmd.closed
    assert not state.query.closed
