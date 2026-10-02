"""Step defs for features/client/domain-client.feature.

The coordinator for the scenario's domain is the test backend
(``_fakes.TestBackend``) served at the endpoint that
``resolve_ch_endpoint(domain, STANDALONE)`` names
(``$ANGZARR_UDS_BASE/ch-<domain>.sock``), so ``DomainClient.for_domain``,
``DomainClient.connect`` and ``DomainClient.from_env`` all reach the same
backend. Each channel the client opens is one backend connection.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from angzarr_client._pb import CommandResponse
from angzarr_client.client import DomainClient, TransportMode, resolve_ch_endpoint
from angzarr_client.errors import ClientError

from ._fakes import (
    GenericCommand,
    TestBackend,
    cover,
    order_type_url,
    root_for,
    route_endpoints,
)

scenarios("features/client/domain-client.feature")

UDS_BASE = "/tmp/angzarr-test-backend"


@dataclass
class _World:
    monkeypatch: Any
    backend: TestBackend | None = None
    client: DomainClient | None = None
    domain: str = ""
    root: UUID | None = None
    command: Any = None
    pages: Any = None
    connections_before_close: int = 0

    def send(self) -> CommandResponse:
        root = self.root or uuid4()
        return (
            self.client.command_handler.command(self.domain, root)
            .with_command(
                order_type_url("AddItem"), GenericCommand(data="via-domain-client")
            )
            .with_sequence(0)
            .execute()
        )

    def query(self, root: UUID):
        return self.client.query.query(self.domain, root)


def _capture(fn):
    try:
        return fn()
    except ClientError as e:
        return e


@pytest.fixture
def state(monkeypatch) -> _World:
    monkeypatch.setenv("ANGZARR_UDS_BASE", UDS_BASE)
    return _World(monkeypatch=monkeypatch)


# ---------------------------------------------------------------------------
# Arrangement
# ---------------------------------------------------------------------------


@given(parsers.parse('a running aggregate coordinator for domain "{domain}"'))
def _given_coordinator(state: _World, domain: str) -> None:
    state.backend = TestBackend(endpoint=f"{UDS_BASE}/ch-{domain}.sock")
    route_endpoints(state.monkeypatch, state.backend)
    state.domain = domain


@given(parsers.parse('a registered aggregate handler for domain "{domain}"'))
def _given_handler(state: _World, domain: str) -> None:
    state.backend.known_domains = {domain}


@given(parsers.parse('an aggregate "{domain}" with root "{root}" has {n:d} events'))
def _given_events(state: _World, domain: str, root: str, n: int) -> None:
    state.backend.seed(cover(domain, root), "ItemAdded", n)
    state.root = root_for(root)


@given("a connected domain client")
def _given_connected(state: _World) -> None:
    state.client = DomainClient.connect(state.backend.endpoint)


@given(
    parsers.parse('environment variable "{name}" is set to the coordinator endpoint')
)
def _given_env(state: _World, name: str) -> None:
    state.monkeypatch.setenv(name, state.backend.endpoint)


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


@when("I create a domain client for the coordinator endpoint")
def _when_create_endpoint(state: _World) -> None:
    _given_connected(state)


@when(parsers.parse('I create a domain client for domain "{domain}"'))
def _when_create_for_domain(state: _World, domain: str) -> None:
    assert (
        resolve_ch_endpoint(domain, TransportMode.STANDALONE) == state.backend.endpoint
    )
    state.client = DomainClient.for_domain(domain, TransportMode.STANDALONE)
    state.domain = domain


@when("I use the command builder to send a command")
def _when_builder_send(state: _World) -> None:
    state.command = _capture(state.send)


@when("I use the query builder to fetch events for that root")
def _when_builder_query(state: _World) -> None:
    assert state.root is not None
    state.pages = _capture(state.query(state.root).get_pages)


@when("I send a command")
def _when_send(state: _World) -> None:
    state.root = uuid4()
    state.command = _capture(state.send)


@when("I query for the resulting events")
def _when_query_resulting(state: _World) -> None:
    state.pages = _capture(state.query(state.root).get_pages)


@when("I close the domain client")
def _when_close(state: _World) -> None:
    assert state.backend.open_connections() == 1
    state.connections_before_close = len(state.backend.channels)
    state.client.close()


@when(parsers.parse('I create a domain client from environment variable "{name}"'))
def _when_from_env(state: _World, name: str) -> None:
    state.client = DomainClient.from_env(name, "unix:///nonexistent/default.sock")


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------


@then("I should be able to query events")
def _then_can_query(state: _World) -> None:
    book = state.query(uuid4()).get_event_book()
    assert len(book.pages) == 0
    assert "GetEventBook" in state.backend.rpc_names()


@then("I should be able to send commands")
def _then_can_send(state: _World) -> None:
    resp = state.send()
    assert len(resp.events.pages) == 1
    assert "HandleCommand" in state.backend.rpc_names()


@then("I should receive a command response")
def _then_command_response(state: _World) -> None:
    assert isinstance(state.command, CommandResponse), state.command
    assert len(state.command.events.pages) == 1
    assert state.command.events.cover.domain == state.domain


@then(parsers.parse("I should receive {n:d} event pages"))
def _then_n_pages(state: _World, n: int) -> None:
    assert not isinstance(state.pages, ClientError), state.pages
    assert len(state.pages) == n


@then("both operations should succeed on the same connection")
def _then_same_connection(state: _World) -> None:
    assert isinstance(state.command, CommandResponse), state.command
    assert not isinstance(state.pages, ClientError), state.pages
    assert len(state.pages) == 1, "query sees the command's event"
    assert len(state.backend.channels) == 1
    assert {cid for _, cid in state.backend.rpcs} == {
        state.backend.channels[0].channel_id
    }
    assert state.backend.rpc_names() == ["HandleCommand", "GetEventBook"]


def _assert_severed(state: _World) -> None:
    assert state.backend.open_connections() == 0, "connection still open after close"
    assert len(state.backend.channels) == state.connections_before_close


@then("subsequent commands should fail with a connection error")
def _then_commands_fail(state: _World) -> None:
    _assert_severed(state)
    result = _capture(state.send)
    assert isinstance(result, ClientError), f"command succeeded: {result!r}"
    assert result.is_connection_error(), result
    assert "HandleCommand" not in state.backend.rpc_names()


@then("subsequent queries should fail with a connection error")
def _then_queries_fail(state: _World) -> None:
    _assert_severed(state)
    result = _capture(state.query(uuid4()).get_event_book)
    assert isinstance(result, ClientError), f"query succeeded: {result!r}"
    assert result.is_connection_error(), result
    assert "GetEventBook" not in state.backend.rpc_names()


@then("the domain client should be connected")
def _then_connected(state: _World) -> None:
    book = state.query(uuid4()).get_event_book()
    assert len(book.pages) == 0
    assert len(state.backend.channels) == 1
    assert state.backend.rpc_names() == ["GetEventBook"]
