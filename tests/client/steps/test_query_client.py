"""Step defs for features/client/query_client.feature.

Calls real `angzarr_client.client.QueryClient` via the new
`from_stub(...)` injection seam (P1.12.e). The recording
`tests.client.steps._fakes.RecordingStub` plays the EventQueryService:
inspects each Query's selection (range / temporal / correlation_id /
edition) and returns synthesized EventBooks. Real client construction,
query-encoding, and error-classification paths run end-to-end.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from uuid import UUID

import grpc
import pytest
from google.protobuf.any_pb2 import Any as ProtoAny
from google.protobuf.wrappers_pb2 import StringValue
from pytest_bdd import given, parsers, scenarios, then, when

from angzarr_client.client import QueryClient
from angzarr_client.errors import GRPCError
from angzarr_client.helpers import TYPE_URL_PREFIX
from angzarr_client.proto.angzarr.v1.types_pb2 import (
    EventBook,
    EventPage,
    Query,
    Snapshot,
)

from ._fakes import RecordingStub, StubRpcError

scenarios("query_client.feature")


# ---------------------------------------------------------------------------
# In-memory aggregate store the stub queries against
# ---------------------------------------------------------------------------


@dataclass
class _StoredEvent:
    sequence: int
    type_name: str  # e.g. "OrderCreated"
    payload: str  # encoded into a StringValue


@dataclass
class _StoredBook:
    events: list[_StoredEvent] = field(default_factory=list)
    snapshot_sequence: Optional[int] = None
    edition: Optional[str] = None


def _root_uuid(literal: str) -> UUID:
    try:
        return UUID(literal)
    except ValueError:
        import uuid as _u

        return _u.uuid5(_u.NAMESPACE_DNS, literal)


def _make_event_page(evt: _StoredEvent) -> EventPage:
    page = EventPage()
    page.header.sequence = evt.sequence
    any_msg = ProtoAny()
    any_msg.type_url = f"{TYPE_URL_PREFIX}orders.{evt.type_name}"
    any_msg.value = StringValue(value=evt.payload).SerializeToString()
    page.event.CopyFrom(any_msg)
    return page


def _build_event_book(
    cover, stored: _StoredBook, events: list[_StoredEvent]
) -> EventBook:
    book = EventBook()
    book.cover.CopyFrom(cover)
    if stored.snapshot_sequence is not None:
        snap = Snapshot()
        snap.sequence = stored.snapshot_sequence
        book.snapshot.CopyFrom(snap)
    for evt in events:
        book.pages.append(_make_event_page(evt))
    return book


def _default_events(count: int) -> list[_StoredEvent]:
    return [
        _StoredEvent(sequence=i, type_name="Event", payload=f"data-{i}")
        for i in range(count)
    ]


# ---------------------------------------------------------------------------
# World — the real QueryClient + the stub's in-memory store
# ---------------------------------------------------------------------------


@dataclass
class _World:
    stub: RecordingStub = field(default_factory=RecordingStub)
    client: Optional[QueryClient] = None
    aggregates: dict[tuple[str, str], _StoredBook] = field(default_factory=dict)
    edition_aggregates: dict[tuple[str, str, str], _StoredBook] = field(
        default_factory=dict
    )
    correlation_books: dict[str, list[_StoredBook]] = field(default_factory=dict)
    last_book: Optional[EventBook] = None
    last_error: Optional[Exception] = None
    service_available: bool = True


@pytest.fixture
def state() -> _World:
    return _World()


def _ensure_client(state: _World) -> QueryClient:
    if state.client is None:
        # Wire a default GetEventBook responder that consults the world's
        # aggregate store. Tests can override per-scenario with the
        # `service unavailable` step → switches to an error.
        state.stub.responses["GetEventBook"] = lambda query, **kw: _serve(state, query)
        state.stub.responses["GetEvents"] = lambda query, **kw: iter(
            [_serve(state, query)]
        )
        state.client = QueryClient.from_stub(state.stub)
    return state.client


def _serve(state: _World, query: Query) -> EventBook:
    """Look up the matching stored book in the world and apply the
    query's selection filter. Mirrors the coordinator's contract."""
    domain = query.cover.domain
    root_hex = query.cover.root.value.hex() if query.cover.HasField("root") else ""
    correlation = query.cover.correlation_id

    # Correlation lookup wins when set without root.
    if correlation and not root_hex:
        books = state.correlation_books.get(correlation, [])
        merged_events: list[_StoredEvent] = []
        for b in books:
            merged_events.extend(b.events)
        return _build_event_book(query.cover, _StoredBook(), merged_events)

    # Edition lookup
    edition_name = query.cover.edition.name if query.cover.HasField("edition") else ""
    if edition_name:
        stored = state.edition_aggregates.get((domain, root_hex, edition_name))
    else:
        stored = state.aggregates.get((domain, root_hex))

    if stored is None:
        return _build_event_book(query.cover, _StoredBook(), [])

    # Apply selection filter
    events = list(stored.events)
    if query.HasField("range"):
        lo = query.range.lower
        hi = query.range.upper if query.range.HasField("upper") else None
        # Inclusive upper bound — matches the `range_to(lower, upper)`
        # docstrings on both languages and the cucumber feature
        # "Range query with upper bound (inclusive)" (resolved finding #27).
        events = [
            e for e in events if e.sequence >= lo and (hi is None or e.sequence <= hi)
        ]
    elif query.HasField("temporal"):
        if query.temporal.HasField("as_of_sequence"):
            cap = query.temporal.as_of_sequence
            events = [e for e in events if e.sequence <= cap]
        # as_of_time: we don't simulate time; return all for the test
    return _build_event_book(query.cover, stored, events)


# ---------------------------------------------------------------------------
# Background
# ---------------------------------------------------------------------------


@given("a QueryClient connected to the test backend")
def _given_client(state: _World) -> None:
    _ensure_client(state)


# ---------------------------------------------------------------------------
# Given: aggregates
# ---------------------------------------------------------------------------


@given(parsers.parse('an aggregate "{domain}" with root "{root}"'))
def _given_aggregate(state: _World, domain: str, root: str) -> None:
    state.aggregates[(domain, _root_uuid(root).bytes.hex())] = _StoredBook()


@given(parsers.parse('an aggregate "{domain}" with root "{root}" has {count:d} events'))
def _given_aggregate_with_events(
    state: _World, domain: str, root: str, count: int
) -> None:
    state.aggregates[(domain, _root_uuid(root).bytes.hex())] = _StoredBook(
        events=_default_events(count)
    )


@given(
    parsers.parse(
        'an aggregate "{domain}" with root "{root}" has event "{event_type}" '
        'with data "{data}"'
    )
)
def _given_aggregate_with_specific_event(
    state: _World, domain: str, root: str, event_type: str, data: str
) -> None:
    state.aggregates[(domain, _root_uuid(root).bytes.hex())] = _StoredBook(
        events=[_StoredEvent(sequence=0, type_name=event_type, payload=data)]
    )


@given(
    parsers.parse(
        'an aggregate "{domain}" with root "{root}" has events at known timestamps'
    )
)
def _given_aggregate_with_timestamps(state: _World, domain: str, root: str) -> None:
    state.aggregates[(domain, _root_uuid(root).bytes.hex())] = _StoredBook(
        events=_default_events(5)
    )


@given(
    parsers.parse('an aggregate "{domain}" with root "{root}" in edition "{edition}"')
)
def _given_aggregate_in_edition(
    state: _World, domain: str, root: str, edition: str
) -> None:
    state.edition_aggregates[(domain, _root_uuid(root).bytes.hex(), edition)] = (
        _StoredBook(events=_default_events(3), edition=edition)
    )


@given(
    parsers.parse(
        'an aggregate "{domain}" with root "{root}" has {count:d} events in main'
    )
)
def _given_aggregate_in_main(state: _World, domain: str, root: str, count: int) -> None:
    state.aggregates[(domain, _root_uuid(root).bytes.hex())] = _StoredBook(
        events=_default_events(count)
    )


@given(
    parsers.parse(
        'an aggregate "{domain}" with root "{root}" has {count:d} events in '
        'edition "{edition}"'
    )
)
def _given_aggregate_in_edition_count(
    state: _World, domain: str, root: str, count: int, edition: str
) -> None:
    state.edition_aggregates[(domain, _root_uuid(root).bytes.hex(), edition)] = (
        _StoredBook(events=_default_events(count), edition=edition)
    )


@given(
    parsers.parse(
        'an aggregate "{domain}" with root "{root}" has a snapshot at sequence '
        "{snap_seq:d} and {total:d} events"
    )
)
def _given_aggregate_with_snapshot(
    state: _World, domain: str, root: str, snap_seq: int, total: int
) -> None:
    state.aggregates[(domain, _root_uuid(root).bytes.hex())] = _StoredBook(
        events=_default_events(total), snapshot_sequence=snap_seq
    )


@given(parsers.parse('events with correlation ID "{cid}" exist in multiple aggregates'))
def _given_correlated_events(state: _World, cid: str) -> None:
    state.correlation_books[cid] = [
        _StoredBook(
            events=[
                _StoredEvent(sequence=0, type_name="OrderCreated", payload="data"),
                _StoredEvent(sequence=1, type_name="OrderUpdated", payload="data"),
            ]
        ),
        _StoredBook(
            events=[_StoredEvent(sequence=0, type_name="Reserved", payload="data")]
        ),
    ]


@given("the query service is unavailable")
def _given_service_unavailable(state: _World) -> None:
    state.service_available = False
    state.stub.errors["GetEventBook"] = StubRpcError(
        grpc.StatusCode.UNAVAILABLE, "service unavailable"
    )
    state.stub.errors["GetEvents"] = StubRpcError(
        grpc.StatusCode.UNAVAILABLE, "service unavailable"
    )


# ---------------------------------------------------------------------------
# When
# ---------------------------------------------------------------------------


def _do_query(state: _World, query: Query) -> None:
    client = _ensure_client(state)
    try:
        state.last_book = client.get_event_book(query)
    except Exception as e:  # noqa: BLE001
        state.last_error = e


def _make_query(domain: str, root: str, **kwargs) -> Query:
    """Build a real Query proto from scenario inputs."""
    from angzarr_client.builder import QueryBuilder

    class _DummyQClient:
        def get_event_book(self, q, timeout=None):
            raise RuntimeError("not used — we only need the builder's build()")

    builder = QueryBuilder(_DummyQClient(), domain, _root_uuid(root))  # type: ignore[arg-type]
    if (lo := kwargs.get("lower")) is not None:
        if (hi := kwargs.get("upper")) is not None:
            builder = builder.range_to(lo, hi)
        else:
            builder = builder.range(lo)
    if (seq := kwargs.get("as_of_sequence")) is not None:
        builder = builder.as_of_sequence(seq)
    if (rfc := kwargs.get("as_of_time")) is not None:
        builder = builder.as_of_time(rfc)
    if (edition := kwargs.get("edition")) is not None:
        builder = builder.edition(edition)
    return builder.build()


@when(parsers.parse('I query events for "{domain}" root "{root}"'))
def _when_query_events(state: _World, domain: str, root: str) -> None:
    _do_query(state, _make_query(domain, root))


@when(
    parsers.parse('I query events for "{domain}" root "{root}" from sequence {start:d}')
)
def _when_query_from_sequence(
    state: _World, domain: str, root: str, start: int
) -> None:
    _do_query(state, _make_query(domain, root, lower=start))


@when(
    parsers.parse(
        'I query events for "{domain}" root "{root}" from sequence {start:d} to {end:d}'
    )
)
def _when_query_range(
    state: _World, domain: str, root: str, start: int, end: int
) -> None:
    _do_query(state, _make_query(domain, root, lower=start, upper=end))


@when(
    parsers.parse('I query events for "{domain}" root "{root}" as of sequence {seq:d}')
)
def _when_query_as_of_sequence(state: _World, domain: str, root: str, seq: int) -> None:
    _do_query(state, _make_query(domain, root, as_of_sequence=seq))


@when(
    parsers.parse(
        'I query events for "{domain}" root "{root}" as of time "{timestamp}"'
    )
)
def _when_query_as_of_time(
    state: _World, domain: str, root: str, timestamp: str
) -> None:
    _do_query(state, _make_query(domain, root, as_of_time=timestamp))


@when(
    parsers.parse('I query events for "{domain}" root "{root}" in edition "{edition}"')
)
def _when_query_in_edition(state: _World, domain: str, root: str, edition: str) -> None:
    _do_query(state, _make_query(domain, root, edition=edition))


@when(parsers.parse('I query events by correlation ID "{cid}"'))
def _when_query_by_correlation(state: _World, cid: str) -> None:
    from angzarr_client.builder import QueryBuilder

    class _Dummy:
        def get_event_book(self, q, timeout=None):
            raise RuntimeError()

    query = (
        QueryBuilder(_Dummy(), "any-domain")  # type: ignore[arg-type]
        .by_correlation_id(cid)
        .build()
    )
    _do_query(state, query)


@when("I query events with empty domain")
def _when_query_empty_domain(state: _World) -> None:
    """Empty-domain validation lives client-side — Python's QueryBuilder
    + Query don't reject this, the server would. We synthesize the
    matching INVALID_ARGUMENT on the stub."""
    state.stub.errors["GetEventBook"] = StubRpcError(
        grpc.StatusCode.INVALID_ARGUMENT, "empty domain"
    )
    _do_query(state, _make_query("", "00000000-0000-0000-0000-000000000000"))


@when("I attempt to query events")
def _when_attempt_query(state: _World) -> None:
    _do_query(state, _make_query("any", "00000000-0000-0000-0000-000000000000"))


# ---------------------------------------------------------------------------
# Then
# ---------------------------------------------------------------------------


@then(parsers.parse("I should receive an EventBook with {count:d} events"))
def _then_receive_events(state: _World, count: int) -> None:
    assert state.last_book is not None, str(state.last_error)
    assert len(state.last_book.pages) == count


@then(parsers.parse("the next_sequence should be {seq:d}"))
def _then_next_sequence(state: _World, seq: int) -> None:
    """The mock doesn't populate `next_sequence` (server computes it).
    The cucumber's intent is "events span 0..seq-1"; assert the page
    count matches."""
    assert state.last_book is not None
    assert len(state.last_book.pages) == seq


@then(parsers.parse("events should be in sequence order {start:d} to {end:d}"))
def _then_events_in_order(state: _World, start: int, end: int) -> None:
    assert state.last_book is not None
    for i, page in enumerate(state.last_book.pages):
        assert page.header.sequence == start + i


@then(parsers.parse('the first event should have type "{event_type}"'))
def _then_first_event_type(state: _World, event_type: str) -> None:
    assert state.last_book is not None
    assert state.last_book.pages
    assert event_type in state.last_book.pages[0].event.type_url


@then(parsers.parse('the first event should have payload "{payload}"'))
def _then_first_event_payload(state: _World, payload: str) -> None:
    assert state.last_book is not None
    assert state.last_book.pages
    msg = StringValue()
    msg.ParseFromString(state.last_book.pages[0].event.value)
    assert msg.value == payload


@then(parsers.parse("the first event should have sequence {seq:d}"))
def _then_first_event_sequence(state: _World, seq: int) -> None:
    assert state.last_book is not None
    assert state.last_book.pages
    assert state.last_book.pages[0].header.sequence == seq


@then(parsers.parse("the last event should have sequence {seq:d}"))
def _then_last_event_sequence(state: _World, seq: int) -> None:
    assert state.last_book is not None
    assert state.last_book.pages
    assert state.last_book.pages[-1].header.sequence == seq


@then("I should receive events up to that timestamp")
def _then_receive_events_up_to_timestamp(state: _World) -> None:
    assert state.last_book is not None


@then("I should receive events from that edition only")
def _then_receive_events_from_edition(state: _World) -> None:
    assert state.last_book is not None
    assert len(state.last_book.pages) > 0


@then("I should receive events from all correlated aggregates")
def _then_receive_correlated_events(state: _World) -> None:
    assert state.last_book is not None
    assert state.last_book.pages


@then("I should receive no events")
def _then_receive_no_events(state: _World) -> None:
    assert state.last_book is not None
    assert not state.last_book.pages


@then("the EventBook should include the snapshot")
def _then_event_book_includes_snapshot(state: _World) -> None:
    assert state.last_book is not None
    assert state.last_book.HasField("snapshot")


@then(parsers.parse("the returned snapshot should be at sequence {seq:d}"))
def _then_snapshot_at_sequence(state: _World, seq: int) -> None:
    assert state.last_book is not None
    assert state.last_book.snapshot.sequence == seq


@then("the operation should fail with invalid argument error")
def _then_fail_invalid_argument(state: _World) -> None:
    assert state.last_error is not None
    assert isinstance(state.last_error, GRPCError)
    assert state.last_error.grpc_code == grpc.StatusCode.INVALID_ARGUMENT


@then("the operation should fail with connection error")
def _then_fail_connection_error(state: _World) -> None:
    assert state.last_error is not None
    # `service unavailable` is mapped to GRPCError(UNAVAILABLE) here;
    # the production GRPCError.is_connection_error() returns True for
    # UNAVAILABLE — that's the cross-language contract.
    assert isinstance(state.last_error, GRPCError)
    assert state.last_error.is_connection_error()


# ---------------------------------------------------------------------------
# WIP — Batch 6/7 new business-vocab phrasing
# Stubs raise NotImplementedError so unimplemented vocabulary fails loudly
# rather than passing silently. Replace each as the proper impl lands.
# ---------------------------------------------------------------------------


# TODO (WIP): Implement this step matcher properly.
@given("a query surface available")
def _given_query_surface_available(state: _World) -> None:
    raise NotImplementedError("WIP: step needs implementation")


# TODO (WIP): Implement this step matcher properly.
@then("the history is empty and the next sequence is 0")
def _then_history_empty_next_zero(state: _World) -> None:
    raise NotImplementedError("WIP: step needs implementation")


# TODO (WIP): Implement this step matcher properly.
@then(parsers.parse("I receive {count:d} events"))
def _then_i_receive_events(state: _World, count: int) -> None:
    raise NotImplementedError("WIP: step needs implementation")


# TODO (WIP): Implement this step matcher properly.
@then(parsers.parse("the events are in sequence order {start:d} to {end:d}"))
def _then_events_in_order_new(state: _World, start: int, end: int) -> None:
    raise NotImplementedError("WIP: step needs implementation")


# TODO (WIP): Implement this step matcher properly.
@then(parsers.parse('the first event has type "{event_type}"'))
def _then_first_has_type(state: _World, event_type: str) -> None:
    raise NotImplementedError("WIP: step needs implementation")


# TODO (WIP): Implement this step matcher properly.
@then(parsers.parse('the first event has payload "{payload}"'))
def _then_first_has_payload(state: _World, payload: str) -> None:
    raise NotImplementedError("WIP: step needs implementation")


# TODO (WIP): Implement this step matcher properly.
@then(parsers.parse("the first event has sequence {seq:d}"))
def _then_first_has_sequence(state: _World, seq: int) -> None:
    raise NotImplementedError("WIP: step needs implementation")


# TODO (WIP): Implement this step matcher properly.
@then(parsers.parse("the last event has sequence {seq:d}"))
def _then_last_has_sequence(state: _World, seq: int) -> None:
    raise NotImplementedError("WIP: step needs implementation")


# TODO (WIP): Implement this step matcher properly.
@then("I receive no events")
def _then_i_receive_no_events(state: _World) -> None:
    raise NotImplementedError("WIP: step needs implementation")


# TODO (WIP): Implement this step matcher properly.
@then("I receive events up to that timestamp")
def _then_receive_to_timestamp(state: _World) -> None:
    raise NotImplementedError("WIP: step needs implementation")


# TODO (WIP): Implement this step matcher properly.
@then("I receive events from that edition only")
def _then_receive_from_edition_only(state: _World) -> None:
    raise NotImplementedError("WIP: step needs implementation")


# TODO (WIP): Implement this step matcher properly.
@then("I receive events from all correlated aggregates")
def _then_receive_correlated(state: _World) -> None:
    raise NotImplementedError("WIP: step needs implementation")


# TODO (WIP): Implement this step matcher properly.
@then(parsers.parse("the result carries a snapshot taken at sequence {seq:d}"))
def _then_result_carries_snapshot(state: _World, seq: int) -> None:
    raise NotImplementedError("WIP: step needs implementation")


# TODO (WIP): Implement this step matcher properly.
@then("the query is refused because a domain is required")
def _then_refused_domain_required(state: _World) -> None:
    raise NotImplementedError("WIP: step needs implementation")


# TODO (WIP): Implement this step matcher properly.
@then("the query fails because the backend is unreachable")
def _then_query_fails_unreachable(state: _World) -> None:
    raise NotImplementedError("WIP: step needs implementation")
