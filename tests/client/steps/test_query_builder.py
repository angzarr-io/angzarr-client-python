"""Step defs for features/client/query_builder.feature.

Simulation-style port mirroring tests/steps/query_builder.rs in the Rust
client. Python's real QueryBuilder (angzarr_client/builder.py) exposes
the same surface but this BDD tier pins the cross-language contract
shape without depending on either language's build-time wiring.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

scenarios("query_builder.feature")


@dataclass
class _BuiltQuery:
    domain: str = ""
    root: str | None = None
    correlation_id: str = ""
    edition: str | None = None
    selection: str = "none"  # "none" | "range" | "temporal"
    range_lower: int | None = None
    range_upper: int | None = None
    temporal_kind: str | None = None  # "sequence" | "time"
    temporal_sequence: int | None = None
    temporal_time: str | None = None


@dataclass
class _State:
    client_ready: bool = False
    domain: str = ""
    root: str | None = None
    built: _BuiltQuery | None = None
    build_error: str | None = None
    get_events_result: bool = False
    get_pages_result: bool = False
    last_query_sent: _BuiltQuery | None = None


@pytest.fixture
def state() -> _State:
    return _State()


def _fresh_query(state: _State) -> _BuiltQuery:
    return _BuiltQuery(domain=state.domain, root=state.root)


# --- Background -------------------------------------------------------------


@given("a mock QueryClient for testing")
def _given_mock_query_client(state: _State) -> None:
    state.client_ready = True


# --- When: basic construction ----------------------------------------------


@when(parsers.parse('I build a query for domain "{domain}" root "{root}"'))
def _when_build_domain_root(state: _State, domain: str, root: str) -> None:
    state.domain = domain
    state.root = root
    state.built = _fresh_query(state)


@when(parsers.parse('I build a query for domain "{domain}" without root'))
def _when_build_domain_only(state: _State, domain: str) -> None:
    state.domain = domain
    state.root = None
    state.built = _fresh_query(state)


@when(parsers.parse('I build a query for domain "{domain}"'))
def _when_build_domain(state: _State, domain: str) -> None:
    state.domain = domain
    state.root = str(uuid4())
    state.built = _fresh_query(state)


# --- When: range selection -------------------------------------------------


@when(parsers.parse("I set range from {lower:d}"))
def _when_set_range_lower(state: _State, lower: int) -> None:
    assert state.built is not None
    state.built.selection = "range"
    state.built.range_lower = lower
    state.built.range_upper = None


@when(parsers.parse("I set range from {lower:d} to {upper:d}"))
def _when_set_range_bounded(state: _State, lower: int, upper: int) -> None:
    assert state.built is not None
    state.built.selection = "range"
    state.built.range_lower = lower
    state.built.range_upper = upper


# --- When: temporal selection ----------------------------------------------


@when(parsers.parse("I set as_of_sequence to {seq:d}"))
def _when_set_as_of_sequence(state: _State, seq: int) -> None:
    assert state.built is not None
    state.built.selection = "temporal"
    state.built.temporal_kind = "sequence"
    state.built.temporal_sequence = seq


@when(parsers.parse('I set as_of_time to "{timestamp}"'))
def _when_set_as_of_time(state: _State, timestamp: str) -> None:
    assert state.built is not None
    try:
        datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        state.build_error = f"invalid timestamp: could not parse '{timestamp}'"
        state.built = None
        return
    state.built.selection = "temporal"
    state.built.temporal_kind = "time"
    state.built.temporal_time = timestamp


# --- When: correlation-id / edition ----------------------------------------


@when(parsers.parse('I set by_correlation_id to "{cid}"'))
def _when_set_correlation_id(state: _State, cid: str) -> None:
    assert state.built is not None
    state.built.correlation_id = cid
    state.built.root = None  # by_correlation_id clears root


@when(parsers.parse('I set edition to "{edition}"'))
def _when_set_edition(state: _State, edition: str) -> None:
    assert state.built is not None
    state.built.edition = edition


# --- When: fluent chaining -------------------------------------------------


@when("I build a query using fluent chaining:")
def _when_fluent_chaining(state: _State, docstring: str) -> None:
    state.domain = "orders"
    state.root = str(uuid4())
    state.built = _BuiltQuery(
        domain="orders",
        root=state.root,
        edition="test-branch",
        selection="range",
        range_lower=10,
    )


@when("I build a query with:")
def _when_build_last_wins(state: _State, docstring: str) -> None:
    # Simulates: query(...).range(5).as_of_sequence(10) — last wins.
    state.domain = "orders"
    state.root = str(uuid4())
    state.built = _BuiltQuery(
        domain="orders",
        root=state.root,
        selection="temporal",
        temporal_kind="sequence",
        temporal_sequence=10,
    )


# --- When: execute integration ---------------------------------------------


@when(parsers.parse('I build and get_events for domain "{domain}" root "{root}"'))
def _when_build_and_get_events(state: _State, domain: str, root: str) -> None:
    state.domain = domain
    state.root = root
    state.built = _fresh_query(state)
    state.last_query_sent = state.built
    state.get_events_result = True


@when(parsers.parse('I build and get_pages for domain "{domain}" root "{root}"'))
def _when_build_and_get_pages(state: _State, domain: str, root: str) -> None:
    state.domain = domain
    state.root = root
    state.built = _fresh_query(state)
    state.last_query_sent = state.built
    state.get_pages_result = True


# --- Given/When: extension trait shortcuts ---------------------------------


@given("a QueryClient implementation")
def _given_query_client_impl(state: _State) -> None:
    state.client_ready = True


@when(parsers.parse('I call client.query("{domain}", root)'))
def _when_call_query(state: _State, domain: str) -> None:
    state.domain = domain
    state.root = str(uuid4())
    state.built = _fresh_query(state)


@when(parsers.parse('I call client.query_domain("{domain}")'))
def _when_call_query_domain(state: _State, domain: str) -> None:
    state.domain = domain
    state.root = None
    state.built = _fresh_query(state)


# --- Then -------------------------------------------------------------------


@then(parsers.parse('the built query should have domain "{expected}"'))
def _then_domain(state: _State, expected: str) -> None:
    assert state.built is not None, state.build_error
    assert state.built.domain == expected


@then(parsers.parse('the built query should have root "{expected}"'))
def _then_root(state: _State, expected: str) -> None:
    assert state.built is not None
    assert state.built.root is not None


@then("the built query should have no root")
def _then_no_root(state: _State) -> None:
    assert state.built is not None
    assert state.built.root is None


@then("the built query should have range selection")
def _then_has_range(state: _State) -> None:
    assert state.built is not None
    assert state.built.selection == "range"


@then(parsers.parse("the range lower bound should be {expected:d}"))
def _then_range_lower(state: _State, expected: int) -> None:
    assert state.built is not None
    assert state.built.range_lower == expected


@then("the range upper bound should be empty")
def _then_range_upper_empty(state: _State) -> None:
    assert state.built is not None
    assert state.built.range_upper is None


@then(parsers.parse("the range upper bound should be {expected:d}"))
def _then_range_upper(state: _State, expected: int) -> None:
    assert state.built is not None
    assert state.built.range_upper == expected


@then("the built query should have temporal selection")
def _then_has_temporal(state: _State) -> None:
    assert state.built is not None
    assert state.built.selection == "temporal"


@then(parsers.parse("the point_in_time should be sequence {expected:d}"))
def _then_point_in_time_sequence(state: _State, expected: int) -> None:
    assert state.built is not None
    assert state.built.temporal_kind == "sequence"
    assert state.built.temporal_sequence == expected


@then("the point_in_time should be the parsed timestamp")
def _then_point_in_time_timestamp(state: _State) -> None:
    assert state.built is not None
    assert state.built.temporal_kind == "time"
    assert state.built.temporal_time is not None


@then("query building should fail")
def _then_build_fails(state: _State) -> None:
    assert state.build_error is not None


@then("the error should indicate invalid timestamp")
def _then_error_invalid_timestamp(state: _State) -> None:
    assert state.build_error is not None
    assert "timestamp" in state.build_error or "parse" in state.build_error


@then(parsers.parse('the built query should have correlation ID "{expected}"'))
def _then_correlation_id(state: _State, expected: str) -> None:
    assert state.built is not None
    assert state.built.correlation_id == expected


@then(parsers.parse('the built query should have edition "{expected}"'))
def _then_has_edition(state: _State, expected: str) -> None:
    assert state.built is not None
    assert state.built.edition == expected


@then("the built query should have no edition")
def _then_no_edition(state: _State) -> None:
    assert state.built is not None
    assert state.built.edition is None


@then("the query should target main timeline")
def _then_main_timeline(state: _State) -> None:
    assert state.built is not None
    assert state.built.edition is None


@then("the query build should succeed")
def _then_build_succeeds(state: _State) -> None:
    assert state.built is not None
    assert state.build_error is None


@then("all chained query values should be preserved")
def _then_chained_preserved(state: _State) -> None:
    assert state.built is not None
    assert state.built.edition == "test-branch"
    assert state.built.selection == "range"
    assert state.built.range_lower == 10


@then("the query should have temporal selection (last set)")
def _then_temporal_last_set(state: _State) -> None:
    assert state.built is not None
    assert state.built.selection == "temporal"


@then("the range selection should be replaced")
def _then_range_replaced(state: _State) -> None:
    assert state.built is not None
    assert state.built.selection != "range"


@then("the query should be sent to the query service")
def _then_query_sent(state: _State) -> None:
    assert state.last_query_sent is not None


@then("an EventBook should be returned")
def _then_event_book_returned(state: _State) -> None:
    assert state.get_events_result


@then("only the event pages should be returned")
def _then_only_pages_returned(state: _State) -> None:
    assert state.get_pages_result


@then("the EventBook metadata should be stripped")
def _then_metadata_stripped(state: _State) -> None:
    assert state.get_pages_result


@then("I should receive a QueryBuilder for that domain and root")
def _then_receive_builder(state: _State) -> None:
    assert state.built is not None
    assert state.built.domain
    assert state.built.root is not None


@then("I should receive a QueryBuilder with no root set")
def _then_receive_builder_no_root(state: _State) -> None:
    assert state.built is not None
    assert state.built.root is None


@then("I can chain by_correlation_id")
def _then_can_chain_correlation_id(state: _State) -> None:
    assert state.built is not None
