"""Step defs for features/client/speculative_client.feature.

Drives a real :class:`SpeculativeClient` against the test backend
(``_fakes.TestBackend``) and checks real state with a real
:class:`QueryClient`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from angzarr_client._pb import (
    CommandBook,
    CommandResponse,
    Cover,
    EventBook,
    ProcessManagerHandleRequest,
    SagaHandleRequest,
    SpeculateCommandHandlerRequest,
    SpeculatePmRequest,
    SpeculateProjectorRequest,
    SpeculateSagaRequest,
    TemporalQuery,
)
from angzarr_client.client import QueryClient, SpeculativeClient
from angzarr_client.errors import ClientError, GRPCError
from angzarr_client.helpers import proto_to_uuid

from ._fakes import (
    GenericEvent,
    TestBackend,
    command_any,
    cover,
    event_any,
    event_data,
    page_seq,
    root_for,
    route_endpoints,
)

scenarios("features/client/speculative_client.feature")


@dataclass
class _World:
    backend: TestBackend = field(default_factory=TestBackend)
    client: SpeculativeClient | None = None
    cover: Cover | None = None
    events: EventBook | None = None
    stored_before: int = 0
    command: Any = None
    commands_ab: list[CommandResponse] = field(default_factory=list)
    projection: Any = None
    saga: Any = None
    pm: Any = None
    real: EventBook | None = None
    error: ClientError | None = None

    def command_book(self, name: str, data: str, count: int = 1) -> CommandBook:
        book = CommandBook()
        book.cover.CopyFrom(self.cover)
        page = book.pages.add()
        page.header.sequence = 0
        page.command.CopyFrom(command_any(name, data, count))
        return book

    def speculate(self, book: CommandBook, as_of: int | None = None):
        req = SpeculateCommandHandlerRequest(command=book)
        if as_of is not None:
            req.point_in_time.CopyFrom(TemporalQuery(as_of_sequence=as_of))
        return _capture(lambda: self.client.command_handler(req))

    def command_ok(self) -> CommandResponse:
        assert isinstance(self.command, CommandResponse), f"failed: {self.command!r}"
        return self.command

    def command_err(self) -> ClientError:
        assert isinstance(self.command, ClientError), f"succeeded: {self.command!r}"
        return self.command

    def emitted_seqs(self) -> list[int]:
        return [page_seq(p) for p in self.command_ok().events.pages]

    def query_real(self, c: Cover) -> EventBook:
        qc = QueryClient.connect(self.backend.endpoint)
        return qc.query(c.domain, proto_to_uuid(c.root)).get_event_book()


def _capture(fn):
    try:
        return fn()
    except ClientError as e:
        return e


def _ok(result):
    assert not isinstance(result, ClientError), f"failed: {result!r}"
    assert result is not None
    return result


def _event_book(c: Cover, n: int) -> EventBook:
    book = EventBook(next_sequence=n)
    book.cover.CopyFrom(c)
    for i in range(n):
        page = book.pages.add()
        page.header.sequence = i
        page.event.CopyFrom(event_any("OrderCreated", f"e{i}"))
    return book


@pytest.fixture
def state(monkeypatch) -> _World:
    world = _World()
    route_endpoints(monkeypatch, world.backend)
    return world


# ---------------------------------------------------------------------------
# Arrangement
# ---------------------------------------------------------------------------


@given("a what-if execution surface available")
def _given_surface(state: _World) -> None:
    state.client = SpeculativeClient.connect(state.backend.endpoint)


@given(parsers.parse('an aggregate "{domain}" with root "{root}" has {n:d} events'))
def _given_n_events(state: _World, domain: str, root: str, n: int) -> None:
    c = cover(domain, root)
    state.backend.seed(c, "ItemAdded", n)
    state.stored_before = state.backend.total_events()
    state.cover = c


@given(
    parsers.parse(
        'a speculative aggregate "{domain}" with root "{root}" has {n:d} events'
    )
)
def _given_spec_aggregate(state: _World, domain: str, root: str, n: int) -> None:
    _given_n_events(state, domain, root, n)


@given(
    parsers.parse('an aggregate "{domain}" with root "{root}" in state "{agg_state}"')
)
def _given_in_state(state: _World, domain: str, root: str, agg_state: str) -> None:
    assert agg_state == "shipped", "only the shipped state is scripted"
    c = cover(domain, root)
    state.backend.seed(c, "OrderCreated", 1)
    state.backend.seed(c, "OrderShipped", 1)
    state.stored_before = state.backend.total_events()
    state.cover = c


@given(parsers.parse('an aggregate "{domain}" with root "{root}"'))
def _given_aggregate(state: _World, domain: str, root: str) -> None:
    state.cover = cover(domain, root)
    state.stored_before = state.backend.total_events()


@given(parsers.parse('events for "{domain}" root "{root}"'))
def _given_events_for(state: _World, domain: str, root: str) -> None:
    state.events = _event_book(cover(domain, root, correlation="corr-1"), 3)
    state.stored_before = state.backend.total_events()


@given(parsers.parse('{n:d} events for "{domain}" root "{root}"'))
def _given_n_events_for(state: _World, n: int, domain: str, root: str) -> None:
    state.events = _event_book(cover(domain, root, correlation="corr-1"), n)
    state.stored_before = state.backend.total_events()


@given(parsers.parse('events with saga origin from "{domain}" aggregate'))
def _given_saga_origin(state: _World, domain: str) -> None:
    c = cover(domain, "origin-root", correlation="corr-1")
    state.cover = c
    state.events = _event_book(c, 2)


@given("correlated events from multiple domains")
def _given_correlated(state: _World) -> None:
    book = _event_book(cover("orders", "wf-order", correlation="workflow-9"), 1)
    page = book.pages.add()
    page.header.sequence = 1
    page.event.CopyFrom(event_any("StockReserved", "inventory"))
    state.events = book
    state.stored_before = state.backend.total_events()


@given("events without correlation ID")
def _given_uncorrelated(state: _World) -> None:
    state.events = _event_book(cover("orders", "wf-order"), 1)


@given("the speculative service is unavailable")
def _given_unavailable(state: _World) -> None:
    state.client = SpeculativeClient.connect("unreachable.invalid:1310")


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


@when(
    parsers.parse('I speculatively execute a command against "{domain}" root "{root}"')
)
def _when_spec_against(state: _World, domain: str, root: str) -> None:
    assert state.cover.domain == domain
    assert state.cover.root.value == root_for(root).bytes
    state.command = state.speculate(state.command_book("AddItem", "what-if"))


@when(parsers.parse("I speculatively execute a command as of sequence {seq:d}"))
def _when_spec_as_of(state: _World, seq: int) -> None:
    state.command = state.speculate(
        state.command_book("AddItem", "historical"), as_of=seq
    )


@when(parsers.parse('I speculatively execute a "{name}" command'))
def _when_spec_named(state: _World, name: str) -> None:
    state.command = state.speculate(state.command_book(name, "what-if"))


@when("I speculatively execute a command with invalid payload")
def _when_spec_invalid(state: _World) -> None:
    book = state.command_book("AddItem", "")
    book.pages[0].command.value = b"\x0a\x05ab"
    state.command = state.speculate(book)


@when("I speculatively execute a command")
def _when_spec_plain(state: _World) -> None:
    state.command = state.speculate(state.command_book("AddItem", "what-if"))


@when(parsers.parse("I speculatively execute a command producing {n:d} events"))
def _when_spec_n(state: _World, n: int) -> None:
    state.command = state.speculate(state.command_book("AddItem", "speculative", n))


@when(parsers.parse("I speculatively execute command {label}"))
def _when_spec_ab(state: _World, label: str) -> None:
    state.commands_ab.append(_ok(state.speculate(state.command_book("AddItem", label))))


@when(parsers.parse('I verify the real events for "{domain}" root "{root}"'))
def _when_verify_real(state: _World, domain: str, root: str) -> None:
    state.real = state.query_real(cover(domain, root))


@when(parsers.parse('I speculatively execute projector "{name}" against those events'))
def _when_spec_projector_against(state: _World, name: str) -> None:
    req = SpeculateProjectorRequest(events=state.events)
    state.projection = _capture(lambda: state.client.projector(req))


@when(parsers.parse('I speculatively execute projector "{name}"'))
def _when_spec_projector(state: _World, name: str) -> None:
    _when_spec_projector_against(state, name)


@when(parsers.parse('I speculatively execute saga "{name}"'))
def _when_spec_saga(state: _World, name: str) -> None:
    req = SpeculateSagaRequest(request=SagaHandleRequest(source=state.events))
    state.saga = _capture(lambda: state.client.saga(req))


@when(parsers.parse('I speculatively execute process manager "{name}"'))
def _when_spec_pm(state: _World, name: str) -> None:
    req = SpeculatePmRequest(request=ProcessManagerHandleRequest(trigger=state.events))
    state.pm = _capture(lambda: state.client.process_manager(req))


@when("I attempt speculative execution")
def _when_attempt(state: _World) -> None:
    result = _capture(
        lambda: state.client.command_handler(SpeculateCommandHandlerRequest())
    )
    state.error = result if isinstance(result, ClientError) else None


@when("I attempt speculative execution with missing parameters")
def _when_attempt_missing(state: _World) -> None:
    _when_attempt(state)


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------


@then("the response should contain the projected events")
def _then_projected(state: _World) -> None:
    assert state.emitted_seqs() == [3]


@then("the events should NOT be persisted")
def _then_not_persisted(state: _World) -> None:
    assert len(state.query_real(state.cover).pages) == 3


@then("the command should execute against the historical state")
def _then_historical(state: _World) -> None:
    assert state.emitted_seqs() == [6], "must continue from sequence 5, not 9"


@then(parsers.parse("the response should reflect state at sequence {seq:d}"))
def _then_reflect_state(state: _World, seq: int) -> None:
    assert state.command_ok().events.next_sequence == seq + 2


@then("the response should indicate rejection")
def _then_rejection(state: _World) -> None:
    err = state.command_err()
    assert err.is_precondition_failed(), f"expected FAILED_PRECONDITION, got {err!r}"


@then(parsers.parse('the rejection reason should be "{reason}"'))
def _then_reason(state: _World, reason: str) -> None:
    err = state.command_err()
    assert isinstance(err, GRPCError)
    assert err.grpc_details == reason


@then("the operation should fail with validation error")
def _then_validation(state: _World) -> None:
    err = state.command_err()
    assert err.is_invalid_argument(), f"expected INVALID_ARGUMENT, got {err!r}"


@then("no events should be produced")
def _then_none_produced(state: _World) -> None:
    state.command_err()
    assert state.backend.total_events() == state.stored_before


@then("the projected execution leaves no trace")
def _then_no_trace(state: _World) -> None:
    assert state.emitted_seqs() == [5]
    assert len(state.backend.stored_pages(state.cover)) == 5
    assert state.backend.total_events() == state.stored_before
    assert "HandleCommand" not in state.backend.rpc_names()


@then("the response should contain the projection")
def _then_projection(state: _World) -> None:
    p = _ok(state.projection)
    assert p.projector == "order-summary"
    assert p.HasField("projection")
    assert p.cover == state.events.cover


@then("no external systems should be updated")
def _then_no_external(state: _World) -> None:
    assert state.backend.projector_runs == []
    assert state.backend.total_events() == state.stored_before


@then(parsers.parse("the projector should process all {n:d} events in order"))
def _then_projector_order(state: _World, n: int) -> None:
    p = _ok(state.projection)
    seen = GenericEvent.FromString(p.projection.value).data
    assert seen == ",".join(str(i) for i in range(n))


@then("the final projection state should be returned")
def _then_final_state(state: _World) -> None:
    p = _ok(state.projection)
    assert p.sequence == page_seq(state.events.pages[-1])


@then("the response should contain the commands the saga would emit")
def _then_saga_commands(state: _World) -> None:
    r = _ok(state.saga)
    assert len(r.commands) == len(state.events.pages)
    assert all(c.cover.domain == "inventory" for c in r.commands)


@then("the commands should NOT be sent to the target domain")
def _then_saga_not_sent(state: _World) -> None:
    assert state.backend.total_events() == state.stored_before
    assert "HandleCommand" not in state.backend.rpc_names()


@then("the response should preserve the saga origin chain")
def _then_origin(state: _World) -> None:
    r = _ok(state.saga)
    assert len(r.commands) > 0
    for i, c in enumerate(r.commands):
        header = c.pages[0].header
        assert header.WhichOneof("sequence_type") == "angzarr_deferred"
        assert header.angzarr_deferred.source == state.cover
        assert header.angzarr_deferred.source_seq == i


@then("the response should contain the PM's command decisions")
def _then_pm_commands(state: _World) -> None:
    r = _ok(state.pm)
    assert len(r.commands) == len(state.events.pages)
    assert all(c.cover.domain == "shipping" for c in r.commands)


@then("the commands should NOT be executed")
def _then_pm_not_executed(state: _World) -> None:
    assert state.backend.total_events() == state.stored_before
    assert "HandleCommand" not in state.backend.rpc_names()


@then("the speculative PM operation should fail")
def _then_pm_fails(state: _World) -> None:
    assert isinstance(state.pm, ClientError), f"pm succeeded: {state.pm!r}"
    assert state.pm.is_invalid_argument(), state.pm


@then("the error should indicate missing correlation ID")
def _then_missing_correlation(state: _World) -> None:
    assert isinstance(state.pm, GRPCError)
    assert "correlation_id" in state.pm.grpc_details, state.pm.grpc_details


@then(parsers.parse("I should receive only {n:d} events"))
def _then_only_n(state: _World, n: int) -> None:
    assert state.real is not None
    assert len(state.real.pages) == n


@then("the speculative events should not be present")
def _then_spec_absent(state: _World) -> None:
    speculative = [event_data(p) for p in state.command_ok().events.pages]
    assert len(speculative) == 2
    real = [event_data(p) for p in state.real.pages]
    assert not set(real) & set(speculative), real


@then("each speculation should start from the same base state")
def _then_same_base(state: _World) -> None:
    assert [page_seq(r.events.pages[0]) for r in state.commands_ab] == [3, 3]


@then("results should be independent")
def _then_independent(state: _World) -> None:
    data = [[event_data(p) for p in r.events.pages] for r in state.commands_ab]
    assert data == [["A"], ["B"]]
    assert state.backend.total_events() == state.stored_before


@then("the speculative operation should fail with connection error")
def _then_connection_error(state: _World) -> None:
    assert state.error is not None, "operation succeeded"
    assert state.error.is_connection_error(), state.error


@then("the speculative operation should fail with invalid argument error")
def _then_invalid_argument(state: _World) -> None:
    assert state.error is not None, "operation succeeded"
    assert state.error.is_invalid_argument(), state.error
