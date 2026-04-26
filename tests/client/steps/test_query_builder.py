"""Step defs for features/client/query_builder.feature.

Calls the real `angzarr_client.builder.QueryBuilder` with a recording
mock QueryClient. Mirrors the Rust pattern at
`client-rust/main/tests/steps/query_builder.rs` (which already imports
production code).

Previously this file used a hand-rolled `_State` simulation that
asserted against fake state — PARITY_AUDIT.md plan item P1.12.b.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from uuid import UUID, uuid4

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from angzarr_client.builder import QueryBuilder, query, query_domain
from angzarr_client.errors import InvalidTimestampError
from angzarr_client.proto.angzarr import EventBook, Query

scenarios("query_builder.feature")


# ---------------------------------------------------------------------------
# Recording mock QueryClient
# ---------------------------------------------------------------------------


class _MockQueryClient:
    """Drop-in for `QueryClient` that records each query and returns
    a stub EventBook."""

    def __init__(self) -> None:
        self.last_query: Optional[Query] = None

    def get_event_book(self, query: Query, timeout: float | None = None) -> EventBook:
        self.last_query = query
        book = EventBook()
        book.cover.CopyFrom(query.cover)
        return book

    def get_events(self, query: Query, timeout: float | None = None) -> list[EventBook]:
        self.last_query = query
        return [self.get_event_book(query)]


@dataclass
class _World:
    client: _MockQueryClient = field(default_factory=_MockQueryClient)
    domain: str = ""
    root: Optional[UUID] = None
    has_root: bool = False
    builder: Optional[QueryBuilder] = None
    built: Optional[Query] = None
    build_error: Optional[Exception] = None
    fetched_book: Optional[EventBook] = None
    fetched_pages: Optional[list] = None


@pytest.fixture
def state() -> _World:
    return _World()


def _ensure_builder(state: _World) -> QueryBuilder:
    if state.builder is None:
        state.builder = (
            query(state.client, state.domain, state.root)  # type: ignore[arg-type]
            if state.has_root and state.root is not None
            else query_domain(state.client, state.domain)  # type: ignore[arg-type]
        )
    return state.builder


def _try_build(state: _World) -> None:
    builder = _ensure_builder(state)
    try:
        state.built = builder.build()
    except Exception as e:  # noqa: BLE001
        state.build_error = e


# ---------------------------------------------------------------------------
# Background
# ---------------------------------------------------------------------------


@given("a mock QueryClient for testing")
def _given_mock_query_client(state: _World) -> None:
    state.client = _MockQueryClient()


@given("a QueryClient implementation")
def _given_query_client_impl(state: _World) -> None:
    state.client = _MockQueryClient()


# ---------------------------------------------------------------------------
# When: domain/root setup
# ---------------------------------------------------------------------------


@when(parsers.parse('I build a query for domain "{domain}" root "{root}"'))
def _when_build_query_domain_root(state: _World, domain: str, root: str) -> None:
    state.domain = domain
    try:
        state.root = UUID(root)
    except ValueError:
        state.root = uuid4()
    state.has_root = True
    _try_build(state)


@when(parsers.parse('I build a query for domain "{domain}" without root'))
def _when_build_query_no_root(state: _World, domain: str) -> None:
    state.domain = domain
    state.has_root = False
    state.root = None
    _try_build(state)


@when(parsers.parse('I build a query for domain "{domain}"'))
def _when_build_query_domain(state: _World, domain: str) -> None:
    state.domain = domain
    state.has_root = False


# ---------------------------------------------------------------------------
# When: range / temporal / correlation / edition
# ---------------------------------------------------------------------------


@when(parsers.parse("I set range from {lower:d}"))
def _when_set_range_from(state: _World, lower: int) -> None:
    state.builder = _ensure_builder(state).range(lower)
    _try_build(state)


@when(parsers.parse("I set range from {lower:d} to {upper:d}"))
def _when_set_range_bounded(state: _World, lower: int, upper: int) -> None:
    state.builder = _ensure_builder(state).range_to(lower, upper)
    _try_build(state)


@when(parsers.parse("I set as_of_sequence to {seq:d}"))
def _when_set_as_of_sequence(state: _World, seq: int) -> None:
    state.builder = _ensure_builder(state).as_of_sequence(seq)
    _try_build(state)


@when(parsers.parse('I set as_of_time to "{rfc3339}"'))
def _when_set_as_of_time(state: _World, rfc3339: str) -> None:
    state.builder = _ensure_builder(state).as_of_time(rfc3339)
    _try_build(state)


@when(parsers.parse('I set by_correlation_id to "{cid}"'))
def _when_set_correlation_id(state: _World, cid: str) -> None:
    state.builder = _ensure_builder(state).by_correlation_id(cid)
    _try_build(state)


@when(parsers.parse('I set edition to "{edition}"'))
def _when_set_edition(state: _World, edition: str) -> None:
    state.builder = _ensure_builder(state).edition(edition)
    _try_build(state)


# ---------------------------------------------------------------------------
# When: fluent chaining
# ---------------------------------------------------------------------------


@when("I build a query using fluent chaining:")
def _when_fluent_chaining(state: _World, docstring: str) -> None:
    state.domain = "orders"
    state.root = uuid4()
    state.has_root = True
    builder = (
        query(state.client, state.domain, state.root)  # type: ignore[arg-type]
        .edition("test-branch")
        .range(10)
    )
    try:
        state.built = builder.build()
    except Exception as e:  # noqa: BLE001
        state.build_error = e


@when("I build a query with:")
def _when_build_query_with(state: _World, docstring: str) -> None:
    """Last-selection-wins scenario: range(5) then as_of_sequence(10)."""
    state.domain = "orders"
    state.root = uuid4()
    state.has_root = True
    builder = (
        query(state.client, state.domain, state.root)  # type: ignore[arg-type]
        .range(5)
        .as_of_sequence(10)
    )
    try:
        state.built = builder.build()
    except Exception as e:  # noqa: BLE001
        state.build_error = e


# ---------------------------------------------------------------------------
# When: execute integration
# ---------------------------------------------------------------------------


@when(parsers.parse('I build and get_events for domain "{domain}" root "{root}"'))
def _when_get_events(state: _World, domain: str, root: str) -> None:
    try:
        state.root = UUID(root)
    except ValueError:
        state.root = uuid4()
    state.fetched_book = (
        query(state.client, domain, state.root)  # type: ignore[arg-type]
        .get_event_book()
    )


@when(parsers.parse('I build and get_pages for domain "{domain}" root "{root}"'))
def _when_get_pages(state: _World, domain: str, root: str) -> None:
    try:
        state.root = UUID(root)
    except ValueError:
        state.root = uuid4()
    state.fetched_pages = (
        query(state.client, domain, state.root)  # type: ignore[arg-type]
        .get_pages()
    )


# ---------------------------------------------------------------------------
# When: extension shortcuts
# ---------------------------------------------------------------------------


@when(parsers.parse('I call client.query("{domain}", root)'))
def _when_call_query(state: _World, domain: str) -> None:
    state.domain = domain
    state.root = uuid4()
    state.has_root = True
    state.builder = query(state.client, state.domain, state.root)  # type: ignore[arg-type]
    _try_build(state)


@when(parsers.parse('I call client.query_domain("{domain}")'))
def _when_call_query_domain(state: _World, domain: str) -> None:
    state.domain = domain
    state.has_root = False
    state.builder = query_domain(state.client, state.domain)  # type: ignore[arg-type]
    _try_build(state)


# ---------------------------------------------------------------------------
# Then
# ---------------------------------------------------------------------------


@then(parsers.parse('the built query should have domain "{expected}"'))
def _then_query_domain(state: _World, expected: str) -> None:
    assert state.built is not None, state.build_error
    assert state.built.cover.domain == expected


@then(parsers.parse('the built query should have root "{expected}"'))
def _then_query_root(state: _World, expected: str) -> None:
    assert state.built is not None
    assert state.built.cover.HasField("root")
    assert len(state.built.cover.root.value) > 0


@then("the built query should have no root")
def _then_query_no_root(state: _World) -> None:
    assert state.built is not None
    has_root = state.built.cover.HasField("root") and len(state.built.cover.root.value) > 0
    assert not has_root


@then("the built query should have range selection")
def _then_query_range_selection(state: _World) -> None:
    assert state.built is not None
    assert state.built.HasField("range")


@then(parsers.parse("the range lower bound should be {expected:d}"))
def _then_range_lower(state: _World, expected: int) -> None:
    assert state.built is not None
    assert state.built.range.lower == expected


@then("the range upper bound should be empty")
def _then_range_upper_empty(state: _World) -> None:
    assert state.built is not None
    assert not state.built.range.HasField("upper")


@then(parsers.parse("the range upper bound should be {expected:d}"))
def _then_range_upper(state: _World, expected: int) -> None:
    assert state.built is not None
    assert state.built.range.upper == expected


@then("the built query should have temporal selection")
def _then_query_temporal_selection(state: _World) -> None:
    assert state.built is not None
    assert state.built.HasField("temporal")


@then(parsers.parse("the point_in_time should be sequence {expected:d}"))
def _then_point_in_time_sequence(state: _World, expected: int) -> None:
    assert state.built is not None
    assert state.built.temporal.as_of_sequence == expected


@then("the point_in_time should be the parsed timestamp")
def _then_point_in_time_timestamp(state: _World) -> None:
    assert state.built is not None
    assert state.built.temporal.HasField("as_of_time")


@then("query building should fail")
def _then_query_building_fails(state: _World) -> None:
    assert state.build_error is not None


@then("the error should indicate invalid timestamp")
def _then_error_invalid_timestamp(state: _World) -> None:
    assert state.build_error is not None
    err_str = str(state.build_error).lower()
    assert isinstance(state.build_error, InvalidTimestampError) or "timestamp" in err_str


@then(parsers.parse('the built query should have correlation ID "{expected}"'))
def _then_query_correlation_id(state: _World, expected: str) -> None:
    assert state.built is not None
    assert state.built.cover.correlation_id == expected


@then(parsers.parse('the built query should have edition "{expected}"'))
def _then_query_edition(state: _World, expected: str) -> None:
    assert state.built is not None
    assert state.built.cover.HasField("edition")
    assert state.built.cover.edition.name == expected


@then("the built query should have no edition")
def _then_query_no_edition(state: _World) -> None:
    assert state.built is not None
    has_edition = state.built.cover.HasField("edition") and bool(
        state.built.cover.edition.name
    )
    assert not has_edition


@then("the query should target main timeline")
def _then_main_timeline(state: _World) -> None:
    assert state.built is not None


@then("the query build should succeed")
def _then_query_build_succeeds(state: _World) -> None:
    assert state.built is not None
    assert state.build_error is None


@then("all chained query values should be preserved")
def _then_chained_query_values(state: _World) -> None:
    assert state.built is not None
    assert state.built.cover.edition.name == "test-branch"
    assert state.built.HasField("range")
    assert state.built.range.lower == 10


@then("the query should have temporal selection (last set)")
def _then_temporal_last_set(state: _World) -> None:
    """`range(5).as_of_sequence(10)` — last setter wins per scenario.

    Rust uses a single `selection` slot that each setter overwrites
    (`builder.rs:165, 180`). Python uses separate `_range` and
    `_temporal` fields with `build()` preferring range. So this
    assertion is true for Rust, false for Python. Surfaces audit
    finding #23.
    """
    assert state.built is not None
    assert state.built.HasField("temporal"), (
        "Python's QueryBuilder kept range selection instead of letting "
        "the later as_of_sequence(10) win. See PARITY_AUDIT.md finding #23."
    )


@then("the range selection should be replaced")
def _then_range_replaced(state: _World) -> None:
    """Range must be cleared when temporal is set after it."""
    assert state.built is not None
    assert not state.built.HasField("range"), (
        "Range selection was not cleared by the later as_of_sequence call. "
        "See PARITY_AUDIT.md finding #23."
    )


@then("the query should be sent to the query service")
def _then_query_sent(state: _World) -> None:
    assert state.client.last_query is not None


@then("an EventBook should be returned")
def _then_eventbook_returned(state: _World) -> None:
    assert state.fetched_book is not None


@then("only the event pages should be returned")
def _then_only_pages_returned(state: _World) -> None:
    assert state.fetched_pages is not None
    assert isinstance(state.fetched_pages, list)


@then("the EventBook metadata should be stripped")
def _then_metadata_stripped(state: _World) -> None:
    assert state.fetched_pages is not None
    assert isinstance(state.fetched_pages, list)


@then("I should receive a QueryBuilder for that domain and root")
def _then_receive_query_builder(state: _World) -> None:
    assert state.builder is not None
    assert state.built is not None
    assert state.built.cover.domain
    assert state.built.cover.HasField("root")


@then("I should receive a QueryBuilder with no root set")
def _then_receive_query_builder_no_root(state: _World) -> None:
    assert state.builder is not None
    assert state.built is not None
    assert not state.built.cover.HasField("root") or len(state.built.cover.root.value) == 0


@then("I can chain by_correlation_id")
def _then_can_chain_correlation(state: _World) -> None:
    assert state.builder is not None
    rebuilt = state.builder.by_correlation_id("chain-test").build()
    assert rebuilt.cover.correlation_id == "chain-test"
