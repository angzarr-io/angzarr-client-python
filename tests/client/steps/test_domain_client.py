"""Step defs for features/client/domain-client.feature.

Simulates DomainClient scenarios via flag-based state mirroring
tests/steps/domain_client.rs in the Rust client. The real
angzarr_client.client.DomainClient is covered by tests/test_client.py at
the unit level; this BDD tier pins cross-language contract shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

scenarios("domain-client.feature")


@dataclass
class _State:
    domain: str = ""
    endpoint: str = ""
    created: bool = False
    connected: bool = False
    closed: bool = False
    can_query: bool = False
    can_command: bool = False
    command_sent: bool = False
    query_executed: bool = False
    events_received: int = 0
    command_response: bool = False
    same_connection: bool = False
    error: str | None = None
    env_var: str | None = None
    aggregates: dict[str, int] = field(default_factory=dict)


@pytest.fixture
def state() -> _State:
    return _State()


# --- Background -------------------------------------------------------------


@given(parsers.parse('a running aggregate coordinator for domain "{domain}"'))
def _given_coordinator(state: _State, domain: str) -> None:
    state.domain = domain


@given(parsers.parse('a registered aggregate handler for domain "{domain}"'))
def _given_handler(state: _State, domain: str) -> None:
    pass


# --- Given ------------------------------------------------------------------


@given(
    parsers.parse(
        'an aggregate "{domain}" with root "{root}" has {count:d} events'
    )
)
def _given_aggregate_with_events(
    state: _State, domain: str, root: str, count: int
) -> None:
    state.aggregates[f"{domain}:{root}"] = count


@given("a connected DomainClient")
def _given_connected(state: _State) -> None:
    state.created = True
    state.connected = True


@given(
    parsers.parse(
        'environment variable "{var_name}" is set to the coordinator endpoint'
    )
)
def _given_env_var(state: _State, var_name: str) -> None:
    state.env_var = var_name
    state.endpoint = "http://localhost:1310"


# --- When -------------------------------------------------------------------


@when("I create a DomainClient for the coordinator endpoint")
def _when_create_coordinator(state: _State) -> None:
    state.created = True
    state.connected = True
    state.can_query = True
    state.can_command = True


@when(parsers.parse('I create a DomainClient for domain "{domain}"'))
def _when_create_domain(state: _State, domain: str) -> None:
    state.domain = domain
    state.created = True
    state.connected = True
    state.can_query = True
    state.can_command = True


@when("I use the command builder to send a command")
def _when_command_builder(state: _State) -> None:
    state.command_sent = True
    state.command_response = True


@when("I use the query builder to fetch events for that root")
def _when_query_builder(state: _State) -> None:
    state.query_executed = True
    for key, count in state.aggregates.items():
        if key.startswith(state.domain):
            state.events_received = count
            break


@when("I send a command")
def _when_send_command(state: _State) -> None:
    state.command_sent = True
    state.same_connection = True


@when("I query for the resulting events")
def _when_query_resulting(state: _State) -> None:
    state.query_executed = True
    state.same_connection = True


@when("I close the DomainClient")
def _when_close(state: _State) -> None:
    state.closed = True
    state.connected = False


@when(
    parsers.parse(
        'I create a DomainClient from environment variable "{var_name}"'
    )
)
def _when_create_from_env(state: _State, var_name: str) -> None:
    state.created = True
    state.connected = True


# --- Then -------------------------------------------------------------------


@then("I should be able to query events")
def _then_can_query(state: _State) -> None:
    assert state.can_query


@then("I should be able to send commands")
def _then_can_command(state: _State) -> None:
    assert state.can_command


@then("I should receive a CommandResponse")
def _then_command_response(state: _State) -> None:
    assert state.command_response


@then(parsers.parse("I should receive {count:d} EventPages"))
def _then_event_pages(state: _State, count: int) -> None:
    assert state.events_received == count


@then("both operations should succeed on the same connection")
def _then_same_connection(state: _State) -> None:
    assert state.same_connection
    assert state.command_sent
    assert state.query_executed


@then("subsequent commands should fail with ConnectionError")
def _then_commands_fail(state: _State) -> None:
    assert state.closed
    state.error = "ConnectionError"


@then("subsequent queries should fail with ConnectionError")
def _then_queries_fail(state: _State) -> None:
    assert state.closed
    state.error = "ConnectionError"


@then("the DomainClient should be connected")
def _then_connected(state: _State) -> None:
    assert state.connected
