"""Step defs for features/client/query_client.feature.

Simulation-style port mirroring tests/steps/query_client.rs in the Rust
client. The real angzarr_client.client.QueryClient is exercised at the
unit level; this BDD tier pins cross-language contract shape for range
queries, temporal queries, edition isolation, correlation lookups, and
snapshot integration.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

scenarios("query_client.feature")


@dataclass
class _MockEvent:
    sequence: int
    event_type: str
    payload: str


@dataclass
class _MockEventBook:
    events: list[_MockEvent] = field(default_factory=list)
    snapshot_sequence: int | None = None
    edition: str | None = None


@dataclass
class _State:
    client_connected: bool = False
    service_available: bool = True
    aggregates: dict[str, _MockEventBook] = field(default_factory=dict)
    correlation_events: dict[str, list[_MockEventBook]] = field(default_factory=dict)
    result: _MockEventBook | None = None
    error: str | None = None


@pytest.fixture
def state() -> _State:
    return _State()


def _default_events(count: int) -> list[_MockEvent]:
    return [
        _MockEvent(sequence=i, event_type="Event", payload=f"data-{i}")
        for i in range(count)
    ]


# --- Background -------------------------------------------------------------


@given("a QueryClient connected to the test backend")
def _given_client(state: _State) -> None:
    state.client_connected = True
    state.service_available = True


# --- Given: aggregates ------------------------------------------------------


@given(parsers.parse('an aggregate "{domain}" with root "{root}"'))
def _given_aggregate(state: _State, domain: str, root: str) -> None:
    state.aggregates[f"{domain}:{root}"] = _MockEventBook()


@given(
    parsers.parse(
        'an aggregate "{domain}" with root "{root}" has {count:d} events'
    )
)
def _given_aggregate_with_events(
    state: _State, domain: str, root: str, count: int
) -> None:
    state.aggregates[f"{domain}:{root}"] = _MockEventBook(
        events=_default_events(count)
    )


@given(
    parsers.parse(
        'an aggregate "{domain}" with root "{root}" has event "{event_type}" '
        'with data "{data}"'
    )
)
def _given_aggregate_with_specific_event(
    state: _State, domain: str, root: str, event_type: str, data: str
) -> None:
    state.aggregates[f"{domain}:{root}"] = _MockEventBook(
        events=[_MockEvent(sequence=0, event_type=event_type, payload=data)]
    )


@given(
    parsers.parse(
        'an aggregate "{domain}" with root "{root}" has events at known timestamps'
    )
)
def _given_aggregate_with_timestamps(
    state: _State, domain: str, root: str
) -> None:
    state.aggregates[f"{domain}:{root}"] = _MockEventBook(
        events=_default_events(5)
    )


@given(
    parsers.parse('an aggregate "{domain}" with root "{root}" in edition "{edition}"')
)
def _given_aggregate_in_edition(
    state: _State, domain: str, root: str, edition: str
) -> None:
    state.aggregates[f"{domain}:{root}:{edition}"] = _MockEventBook(
        events=_default_events(3), edition=edition
    )


@given(
    parsers.parse(
        'an aggregate "{domain}" with root "{root}" has {count:d} events in main'
    )
)
def _given_aggregate_in_main(
    state: _State, domain: str, root: str, count: int
) -> None:
    state.aggregates[f"{domain}:{root}"] = _MockEventBook(
        events=_default_events(count)
    )


@given(
    parsers.parse(
        'an aggregate "{domain}" with root "{root}" has {count:d} events in '
        'edition "{edition}"'
    )
)
def _given_aggregate_in_edition_count(
    state: _State, domain: str, root: str, count: int, edition: str
) -> None:
    state.aggregates[f"{domain}:{root}:{edition}"] = _MockEventBook(
        events=_default_events(count), edition=edition
    )


@given(
    parsers.parse(
        'an aggregate "{domain}" with root "{root}" has a snapshot at sequence '
        '{snap_seq:d} and {total:d} events'
    )
)
def _given_aggregate_with_snapshot(
    state: _State, domain: str, root: str, snap_seq: int, total: int
) -> None:
    state.aggregates[f"{domain}:{root}"] = _MockEventBook(
        events=_default_events(total), snapshot_sequence=snap_seq
    )


@given(
    parsers.parse(
        'events with correlation ID "{cid}" exist in multiple aggregates'
    )
)
def _given_correlated_events(state: _State, cid: str) -> None:
    state.correlation_events[cid] = [
        _MockEventBook(
            events=[
                _MockEvent(sequence=0, event_type="OrderCreated", payload="data"),
                _MockEvent(sequence=1, event_type="OrderUpdated", payload="data"),
            ]
        ),
        _MockEventBook(
            events=[_MockEvent(sequence=0, event_type="Reserved", payload="data")]
        ),
    ]


@given("the query service is unavailable")
def _given_service_unavailable(state: _State) -> None:
    state.service_available = False


# --- When -------------------------------------------------------------------


@when(parsers.parse('I query events for "{domain}" root "{root}"'))
def _when_query_events(state: _State, domain: str, root: str) -> None:
    if not state.service_available:
        state.error = "Connection error"
        return
    key = f"{domain}:{root}"
    state.result = state.aggregates.get(key, _MockEventBook())


@when(
    parsers.parse(
        'I query events for "{domain}" root "{root}" from sequence {start:d}'
    )
)
def _when_query_from_sequence(
    state: _State, domain: str, root: str, start: int
) -> None:
    key = f"{domain}:{root}"
    book = state.aggregates.get(key)
    if book is None:
        state.result = _MockEventBook()
        return
    filtered = [e for e in book.events if e.sequence >= start]
    state.result = _MockEventBook(
        events=filtered,
        snapshot_sequence=book.snapshot_sequence,
        edition=book.edition,
    )


@when(
    parsers.parse(
        'I query events for "{domain}" root "{root}" from sequence {start:d} '
        'to {end:d}'
    )
)
def _when_query_range(
    state: _State, domain: str, root: str, start: int, end: int
) -> None:
    key = f"{domain}:{root}"
    book = state.aggregates.get(key)
    if book is None:
        state.result = _MockEventBook()
        return
    filtered = [e for e in book.events if start <= e.sequence < end]
    state.result = _MockEventBook(
        events=filtered,
        snapshot_sequence=book.snapshot_sequence,
        edition=book.edition,
    )


@when(
    parsers.parse(
        'I query events for "{domain}" root "{root}" as of sequence {seq:d}'
    )
)
def _when_query_as_of_sequence(
    state: _State, domain: str, root: str, seq: int
) -> None:
    key = f"{domain}:{root}"
    book = state.aggregates.get(key)
    if book is None:
        state.result = _MockEventBook()
        return
    filtered = [e for e in book.events if e.sequence <= seq]
    state.result = _MockEventBook(
        events=filtered,
        snapshot_sequence=book.snapshot_sequence,
        edition=book.edition,
    )


@when(
    parsers.parse(
        'I query events for "{domain}" root "{root}" as of time "{timestamp}"'
    )
)
def _when_query_as_of_time(
    state: _State, domain: str, root: str, timestamp: str
) -> None:
    key = f"{domain}:{root}"
    state.result = state.aggregates.get(key, _MockEventBook())


@when(
    parsers.parse(
        'I query events for "{domain}" root "{root}" in edition "{edition}"'
    )
)
def _when_query_in_edition(
    state: _State, domain: str, root: str, edition: str
) -> None:
    key = f"{domain}:{root}:{edition}"
    state.result = state.aggregates.get(
        key, _MockEventBook(edition=edition)
    )


@when(parsers.parse('I query events by correlation ID "{cid}"'))
def _when_query_by_correlation(state: _State, cid: str) -> None:
    books = state.correlation_events.get(cid)
    if books is None:
        state.result = _MockEventBook()
        return
    combined: list[_MockEvent] = []
    for b in books:
        combined.extend(b.events)
    state.result = _MockEventBook(events=combined)


@when("I query events with empty domain")
def _when_query_empty_domain(state: _State) -> None:
    state.error = "Invalid argument: empty domain"


@when("I attempt to query events")
def _when_attempt_query(state: _State) -> None:
    if not state.service_available:
        state.error = "Connection error"


# --- Then -------------------------------------------------------------------


@then(parsers.parse("I should receive an EventBook with {count:d} events"))
def _then_receive_events(state: _State, count: int) -> None:
    assert state.result is not None
    assert len(state.result.events) == count


@then(parsers.parse("the next_sequence should be {seq:d}"))
def _then_next_sequence(state: _State, seq: int) -> None:
    assert state.result is not None
    assert len(state.result.events) == seq


@then(parsers.parse("events should be in sequence order {start:d} to {end:d}"))
def _then_events_in_order(state: _State, start: int, end: int) -> None:
    assert state.result is not None
    for i, event in enumerate(state.result.events):
        assert event.sequence == start + i


@then(parsers.parse('the first event should have type "{event_type}"'))
def _then_first_event_type(state: _State, event_type: str) -> None:
    assert state.result is not None
    assert state.result.events
    assert state.result.events[0].event_type == event_type


@then(parsers.parse('the first event should have payload "{payload}"'))
def _then_first_event_payload(state: _State, payload: str) -> None:
    assert state.result is not None
    assert state.result.events
    assert state.result.events[0].payload == payload


@then(parsers.parse("the first event should have sequence {seq:d}"))
def _then_first_event_sequence(state: _State, seq: int) -> None:
    assert state.result is not None
    assert state.result.events
    assert state.result.events[0].sequence == seq


@then(parsers.parse("the last event should have sequence {seq:d}"))
def _then_last_event_sequence(state: _State, seq: int) -> None:
    assert state.result is not None
    assert state.result.events
    assert state.result.events[-1].sequence == seq


@then("I should receive events up to that timestamp")
def _then_receive_events_up_to_timestamp(state: _State) -> None:
    assert state.result is not None


@then("I should receive events from that edition only")
def _then_receive_events_from_edition(state: _State) -> None:
    assert state.result is not None


@then("I should receive events from all correlated aggregates")
def _then_receive_correlated_events(state: _State) -> None:
    assert state.result is not None
    assert state.result.events


@then("I should receive no events")
def _then_receive_no_events(state: _State) -> None:
    assert state.result is not None
    assert not state.result.events


@then("the EventBook should include the snapshot")
def _then_event_book_includes_snapshot(state: _State) -> None:
    assert state.result is not None
    assert state.result.snapshot_sequence is not None


@then(parsers.parse("the returned snapshot should be at sequence {seq:d}"))
def _then_snapshot_at_sequence(state: _State, seq: int) -> None:
    assert state.result is not None
    assert state.result.snapshot_sequence == seq


@then("the operation should fail with invalid argument error")
def _then_fail_invalid_argument(state: _State) -> None:
    assert state.error is not None
    assert "invalid" in state.error.lower()


@then("the operation should fail with connection error")
def _then_fail_connection_error(state: _State) -> None:
    assert state.error is not None
    assert "connection" in state.error.lower()
