"""Step defs for features/client/query_client.feature.

Drives a real :class:`QueryClient` (through the fluent
:class:`QueryBuilder`) against the test backend (``_fakes.TestBackend``).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from google.protobuf.timestamp_pb2 import Timestamp
from pytest_bdd import given, parsers, scenarios, then, when

from angzarr_client._pb import EventBook
from angzarr_client.client import QueryClient
from angzarr_client.errors import ClientError, GRPCError

from ._fakes import (
    TestBackend,
    cover,
    event_data,
    page_seq,
    root_for,
    route_endpoints,
    short_type,
    timestamp,
)

scenarios("features/client/query_client.feature")


@dataclass
class _World:
    backend: TestBackend = field(default_factory=TestBackend)
    client: QueryClient | None = None
    book: EventBook | ClientError | None = None
    books: list[EventBook] | None = None
    cutoff: Timestamp | None = None
    expected_edition_data: list[str] = field(default_factory=list)
    correlated_domains: list[str] = field(default_factory=list)

    def ok(self) -> EventBook:
        assert isinstance(self.book, EventBook), f"query failed: {self.book!r}"
        return self.book

    def err(self) -> ClientError:
        assert isinstance(self.book, ClientError), f"query succeeded: {self.book!r}"
        return self.book

    def fetch(self, builder) -> None:
        try:
            self.book = builder.get_event_book()
        except ClientError as e:
            self.book = e


def _seqs(book: EventBook) -> list[int]:
    return [page_seq(p) for p in book.pages]


@pytest.fixture
def state(monkeypatch) -> _World:
    world = _World()
    route_endpoints(monkeypatch, world.backend)
    return world


# ---------------------------------------------------------------------------
# Arrangement
# ---------------------------------------------------------------------------


@given("a query surface available")
def _given_surface(state: _World) -> None:
    state.client = QueryClient.connect(state.backend.endpoint)


@given(parsers.parse('an aggregate "{domain}" with root "{root}"'))
def _given_unknown(state: _World, domain: str, root: str) -> None:
    assert state.backend.stored_pages(cover(domain, root)) == []


@given(parsers.parse('an aggregate "{domain}" with root "{root}" has {n:d} events'))
def _given_n_events(state: _World, domain: str, root: str, n: int) -> None:
    state.backend.seed(cover(domain, root), "ItemAdded", n)


@given(
    parsers.parse(
        'an aggregate "{domain}" with root "{root}" has event "{name}" with data "{data}"'
    )
)
def _given_event_with_data(
    state: _World, domain: str, root: str, name: str, data: str
) -> None:
    state.backend.seed_event(cover(domain, root), name, data)


@given(
    parsers.parse(
        'an aggregate "{domain}" with root "{root}" has events at known timestamps'
    )
)
def _given_timestamps(state: _World, domain: str, root: str) -> None:
    for i, at in enumerate(
        [
            "2024-01-15T10:00:00Z",
            "2024-01-15T10:15:00Z",
            "2024-01-15T10:30:00Z",
            "2024-01-15T10:45:00Z",
            "2024-01-15T11:00:00Z",
        ]
    ):
        state.backend.seed_event(
            cover(domain, root), "ItemAdded", f"t{i}", timestamp(at)
        )


@given(
    parsers.parse('an aggregate "{domain}" with root "{root}" in edition "{edition}"')
)
def _given_in_edition(state: _World, domain: str, root: str, edition: str) -> None:
    state.backend.seed_event(cover(domain, root), "ItemAdded", "main-0")
    state.backend.seed_event(cover(domain, root, edition), "ItemAdded", "edition-0")
    state.expected_edition_data = ["edition-0"]


@given(
    parsers.parse('an aggregate "{domain}" with root "{root}" has {n:d} events in main')
)
def _given_main(state: _World, domain: str, root: str, n: int) -> None:
    state.backend.seed(cover(domain, root), "ItemAdded", n)


@given(
    parsers.parse(
        'an aggregate "{domain}" with root "{root}" has {n:d} events in edition "{edition}"'
    )
)
def _given_edition_n(
    state: _World, domain: str, root: str, n: int, edition: str
) -> None:
    state.backend.seed(cover(domain, root, edition), "ItemAdded", n)


@given(parsers.parse('events with correlation ID "{cid}" exist in multiple aggregates'))
def _given_correlated(state: _World, cid: str) -> None:
    b = state.backend
    b.seed(cover("orders", "corr-order", correlation=cid), "OrderCreated", 1)
    b.seed(cover("inventory", "corr-stock", correlation=cid), "StockReserved", 1)
    b.seed(cover("shipping", "corr-ship", correlation=cid), "ShipmentCreated", 1)
    b.seed(cover("orders", "unrelated", correlation="other-flow"), "OrderCreated", 1)
    state.correlated_domains = ["inventory", "orders", "shipping"]


@given(
    parsers.parse(
        'an aggregate "{domain}" with root "{root}" has a snapshot at sequence '
        "{snap:d} and {n:d} events"
    )
)
def _given_snapshot(state: _World, domain: str, root: str, snap: int, n: int) -> None:
    state.backend.seed(cover(domain, root), "ItemAdded", n)
    state.backend.seed_snapshot(cover(domain, root), snap)


@given("the query service is unavailable")
def _given_unavailable(state: _World) -> None:
    state.client = QueryClient.connect("unreachable.invalid:1310")


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


@when(parsers.parse('I query events for "{domain}" root "{root}"'))
def _when_query(state: _World, domain: str, root: str) -> None:
    state.fetch(state.client.query(domain, root_for(root)))


@when(
    parsers.parse(
        'I query events for "{domain}" root "{root}" from sequence {lo:d} to {hi:d}'
    )
)
def _when_query_range(state: _World, domain: str, root: str, lo: int, hi: int) -> None:
    state.fetch(state.client.query(domain, root_for(root)).range_to(lo, hi))


@when(parsers.parse('I query events for "{domain}" root "{root}" from sequence {lo:d}'))
def _when_query_from(state: _World, domain: str, root: str, lo: int) -> None:
    state.fetch(state.client.query(domain, root_for(root)).range(lo))


@when(
    parsers.parse('I query events for "{domain}" root "{root}" as of sequence {seq:d}')
)
def _when_query_as_of_seq(state: _World, domain: str, root: str, seq: int) -> None:
    state.fetch(state.client.query(domain, root_for(root)).as_of_sequence(seq))


@when(parsers.parse('I query events for "{domain}" root "{root}" as of time "{at}"'))
def _when_query_as_of_time(state: _World, domain: str, root: str, at: str) -> None:
    state.cutoff = timestamp(at)
    state.fetch(state.client.query(domain, root_for(root)).as_of_time(at))


@when(
    parsers.parse('I query events for "{domain}" root "{root}" in edition "{edition}"')
)
def _when_query_edition(state: _World, domain: str, root: str, edition: str) -> None:
    state.fetch(state.client.query(domain, root_for(root)).edition(edition))


@when(parsers.parse('I query events by correlation ID "{cid}"'))
def _when_query_correlation(state: _World, cid: str) -> None:
    state.books = (
        state.client.query_domain("orders").by_correlation_id(cid).get_events()
    )


@when("I query events with empty domain")
def _when_query_empty_domain(state: _World) -> None:
    state.fetch(state.client.query("", root_for("any")))


@when("I attempt to query events")
def _when_attempt(state: _World) -> None:
    state.fetch(state.client.query("orders", root_for("any")))


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------


@then(parsers.parse("the history is empty and the next sequence is {nxt:d}"))
def _then_empty(state: _World, nxt: int) -> None:
    book = state.ok()
    assert len(book.pages) == 0
    assert book.next_sequence == nxt


@then(parsers.parse("I receive {n:d} events"))
def _then_n_events(state: _World, n: int) -> None:
    assert len(state.ok().pages) == n


@then(parsers.parse("the events are in sequence order {lo:d} to {hi:d}"))
def _then_order(state: _World, lo: int, hi: int) -> None:
    assert _seqs(state.ok()) == list(range(lo, hi + 1))


@then(parsers.parse('the first event has type "{name}"'))
def _then_first_type(state: _World, name: str) -> None:
    assert short_type(state.ok().pages[0].event) == name


@then(parsers.parse('the first event has payload "{data}"'))
def _then_first_payload(state: _World, data: str) -> None:
    assert event_data(state.ok().pages[0]) == data


@then(parsers.parse("the first event has sequence {seq:d}"))
def _then_first_seq(state: _World, seq: int) -> None:
    assert _seqs(state.ok())[0] == seq


@then(parsers.parse("the last event has sequence {seq:d}"))
def _then_last_seq(state: _World, seq: int) -> None:
    assert _seqs(state.ok())[-1] == seq


@then("I receive no events")
def _then_none(state: _World) -> None:
    if state.books is not None:
        assert sum(len(b.pages) for b in state.books) == 0
    else:
        assert len(state.ok().pages) == 0


@then("I receive events up to that timestamp")
def _then_up_to_time(state: _World) -> None:
    pages = state.ok().pages
    assert [event_data(p) for p in pages] == ["t0", "t1", "t2"]
    cutoff = (state.cutoff.seconds, state.cutoff.nanos)
    assert all((p.created_at.seconds, p.created_at.nanos) <= cutoff for p in pages)


@then("I receive events from that edition only")
def _then_edition_only(state: _World) -> None:
    assert [event_data(p) for p in state.ok().pages] == state.expected_edition_data


@then("I receive events from all correlated aggregates")
def _then_correlated(state: _World) -> None:
    assert state.books is not None
    assert sorted(b.cover.domain for b in state.books) == state.correlated_domains
    assert all(len(b.pages) == 1 for b in state.books)


@then(parsers.parse("the result carries a snapshot taken at sequence {seq:d}"))
def _then_snapshot(state: _World, seq: int) -> None:
    book = state.ok()
    assert book.HasField("snapshot")
    assert book.snapshot.sequence == seq


@then("the query is refused because a domain is required")
def _then_domain_required(state: _World) -> None:
    err = state.err()
    assert err.is_invalid_argument(), f"expected INVALID_ARGUMENT, got {err!r}"
    assert isinstance(err, GRPCError)
    assert "domain" in err.grpc_details, err.grpc_details


@then("the query fails because the backend is unreachable")
def _then_unreachable(state: _World) -> None:
    err = state.err()
    assert err.is_connection_error(), f"expected connection error, got {err!r}"
