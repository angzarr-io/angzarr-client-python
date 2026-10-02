"""Step defs for features/client/aggregate_client.feature.

Drives a real :class:`CommandHandlerClient` (through the fluent
:class:`CommandBuilder`) against the test backend
(``_fakes.TestBackend``), and reads history back with a real
:class:`QueryClient`.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import grpc
import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from angzarr_client._pb import CommandRequest, CommandResponse, EventBook, SyncMode
from angzarr_client.builder import CommandBuilder
from angzarr_client.client import CommandHandlerClient, QueryClient
from angzarr_client.errors import ClientError, GRPCError

from ._fakes import (
    CreateOrder,
    GenericCommand,
    TestBackend,
    cover,
    order_type_url,
    page_seq,
    root_for,
    route_endpoints,
    short_type,
)

scenarios("features/client/aggregate_client.feature")


class _TruncatedPayload:
    """A payload whose bytes are not a decodable protobuf message: a
    length-delimited field 1 announcing 5 bytes but carrying 2."""

    def SerializeToString(self) -> bytes:
        return b"\x0a\x05ab"


Outcome = CommandResponse | ClientError


@dataclass
class _World:
    backend: TestBackend = field(default_factory=TestBackend)
    client: CommandHandlerClient | None = None
    domain: str = ""
    root: UUID = field(default_factory=uuid4)
    last_sequence: int = 0
    result: Outcome | None = None
    concurrent: list[Outcome] = field(default_factory=list)
    looked_up_sequence: int | None = None
    read_back: EventBook | None = None

    def query_client(self) -> QueryClient:
        return QueryClient.connect(self.backend.endpoint)

    def builder(self, sequence: int, correlation: str | None = None) -> CommandBuilder:
        b = self.client.command(self.domain, self.root).with_sequence(sequence)
        return b.with_correlation_id(correlation) if correlation else b

    def send_generic(
        self,
        name: str,
        sequence: int,
        count: int = 1,
        mode: SyncMode = SyncMode.SYNC_MODE_ASYNC,
        correlation: str | None = None,
    ) -> Outcome:
        builder = self.builder(sequence, correlation).with_command(
            order_type_url(name), GenericCommand(data=f"{name}-data", count=count)
        )
        return _capture(lambda: builder.execute(sync_mode=mode))

    def send_create(self, customer_id: str, sequence: int) -> Outcome:
        builder = self.builder(sequence).with_command(
            order_type_url("CreateOrder"),
            CreateOrder(order_id="o-1", customer_id=customer_id),
        )
        return _capture(builder.execute)

    def ok(self) -> CommandResponse:
        assert isinstance(
            self.result, CommandResponse
        ), f"command failed: {self.result!r}"
        return self.result

    def err(self) -> ClientError:
        assert isinstance(
            self.result, ClientError
        ), f"command accepted: {self.result!r}"
        return self.result


def _capture(fn) -> Outcome:
    try:
        return fn()
    except ClientError as e:
        return e


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


@given("a client connected to the test backend")
def _given_connected(state: _World) -> None:
    state.backend.known_domains = {"orders", "inventory"}
    state.backend.projector_domains.add("orders")
    state.client = CommandHandlerClient.connect(state.backend.endpoint)


@given(parsers.parse('a new aggregate root in domain "{domain}"'))
def _given_new_root(state: _World, domain: str) -> None:
    state.domain = domain
    state.root = uuid4()
    state.last_sequence = 0


@given(parsers.parse('an aggregate "{domain}" with root "{root}" at sequence {seq:d}'))
def _given_aggregate_at(state: _World, domain: str, root: str, seq: int) -> None:
    state.backend.seed(cover(domain, root), "ItemAdded", seq)
    state.domain = domain
    state.root = root_for(root)
    state.last_sequence = seq


@given(parsers.parse('an aggregate "{domain}" with root "{root}"'))
def _given_aggregate(state: _World, domain: str, root: str) -> None:
    state.domain = domain
    state.root = root_for(root)


@given(parsers.parse('no aggregate exists for domain "{domain}" root "{root}"'))
def _given_no_aggregate(state: _World, domain: str, root: str) -> None:
    assert state.backend.stored_pages(cover(domain, root)) == []
    state.domain = domain
    state.root = root_for(root)


@given(parsers.parse('projectors are configured for "{domain}" domain'))
def _given_projectors(state: _World, domain: str) -> None:
    state.backend.projector_domains.add(domain)


@given(parsers.parse('sagas are configured for "{domain}" domain'))
def _given_sagas(state: _World, domain: str) -> None:
    state.backend.sagas[domain] = "inventory"


@given("the aggregate service is unavailable")
def _given_unavailable(state: _World) -> None:
    state.client = CommandHandlerClient.connect("unreachable.invalid:1310")
    state.domain = "orders"
    state.root = uuid4()


@given("the aggregate service does not respond in time")
def _given_slow(state: _World) -> None:
    state.backend.response_delay = 3.0
    state.domain = "orders"
    state.root = uuid4()


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


@when(parsers.parse('I send a "{name}" command with data "{data}"'))
def _when_send_named_with_data(state: _World, name: str, data: str) -> None:
    assert name == "CreateOrder", "the scripted aggregate creates via CreateOrder"
    state.result = state.send_create(data, state.last_sequence)


@when(parsers.parse('I send an "{name}" command at sequence {seq:d}'))
def _when_send_named_at(state: _World, name: str, seq: int) -> None:
    state.result = state.send_generic(name, seq)


@when(parsers.parse('I send a command tagged with correlation ID "{cid}"'))
def _when_send_correlated(state: _World, cid: str) -> None:
    state.result = state.send_generic("AddItem", state.last_sequence, correlation=cid)


@when(parsers.parse("I send a command at sequence {seq:d}"))
def _when_send_at(state: _World, seq: int) -> None:
    state.result = state.send_generic("AddItem", seq)


@when(parsers.parse("two commands are sent concurrently at sequence {seq:d}"))
def _when_concurrent(state: _World, seq: int) -> None:
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(state.send_generic, "AddItem", seq) for _ in range(2)]
        state.concurrent = [f.result() for f in futures]


@when(parsers.parse('I look up the current sequence for "{domain}" root "{root}"'))
def _when_lookup(state: _World, domain: str, root: str) -> None:
    book = state.query_client().query(domain, root_for(root)).get_event_book()
    state.looked_up_sequence = book.next_sequence


@when("I retry the command at that sequence")
def _when_retry(state: _World) -> None:
    assert state.looked_up_sequence is not None
    state.result = state.send_generic("AddItem", state.looked_up_sequence)


@when("I send a command without waiting for downstream work")
def _when_send_async(state: _World) -> None:
    state.result = state.send_generic("AddItem", 0, mode=SyncMode.SYNC_MODE_ASYNC)


@when("I send a command and wait for projectors")
def _when_send_simple(state: _World) -> None:
    state.result = state.send_generic("AddItem", 0, mode=SyncMode.SYNC_MODE_SIMPLE)


@when("I send a command and wait for downstream sagas")
def _when_send_cascade(state: _World) -> None:
    state.result = state.send_generic("AddItem", 0, mode=SyncMode.SYNC_MODE_CASCADE)


@when("I send a command with a malformed payload")
def _when_send_malformed(state: _World) -> None:
    builder = state.builder(0).with_command(
        order_type_url("CreateOrder"),
        _TruncatedPayload(),  # type: ignore[arg-type]
    )
    state.result = _capture(builder.execute)


@when("I send a command missing required fields")
def _when_send_missing_fields(state: _World) -> None:
    state.result = state.send_create("", 0)


@when(parsers.parse('I send a command to domain "{domain}"'))
def _when_send_to_domain(state: _World, domain: str) -> None:
    state.domain = domain
    state.root = uuid4()
    state.result = state.send_generic("AddItem", 0)


@when(parsers.parse("I send a command that produces {n:d} events"))
def _when_send_multi(state: _World, n: int) -> None:
    state.result = state.send_generic("AddItem", state.last_sequence, count=n)


@when(parsers.parse('I read back the events for "{domain}" root "{root}"'))
def _when_read_back(state: _World, domain: str, root: str) -> None:
    state.read_back = (
        state.query_client().query(domain, root_for(root)).get_event_book()
    )


@when("I attempt to send a command")
def _when_attempt(state: _World) -> None:
    state.result = state.send_generic("AddItem", 0)


@when("I send a command with a short timeout")
def _when_short_timeout(state: _World) -> None:
    book = (
        state.builder(0)
        .with_command(order_type_url("AddItem"), GenericCommand(data="slow"))
        .build()
    )
    request = CommandRequest(command=book, sync_mode=SyncMode.SYNC_MODE_ASYNC)
    state.result = _capture(lambda: state.client.handle_command(request, timeout=0.2))


@when(parsers.parse('I send a "{name}" command for root "{root}" at sequence {seq:d}'))
def _when_send_named_for_root(state: _World, name: str, root: str, seq: int) -> None:
    assert name == "CreateOrder", "the scripted aggregate creates via CreateOrder"
    state.root = root_for(root)
    state.result = state.send_create("customer-1", seq)


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------


@then("the command is accepted")
def _then_accepted(state: _World) -> None:
    assert len(state.ok().events.pages) > 0, "accepted command emitted no events"


@then(parsers.parse('a single "{name}" event is recorded'))
def _then_single_event(state: _World, name: str) -> None:
    book = state.ok().events
    assert len(book.pages) == 1
    assert short_type(book.pages[0].event) == name
    assert len(state.backend.stored_pages(book.cover)) == 1


@then(parsers.parse("the new events continue the history from sequence {seq:d}"))
def _then_continue_from(state: _World, seq: int) -> None:
    assert _seqs(state.ok().events)[0] == seq


@then(parsers.parse('the resulting events carry correlation ID "{cid}"'))
def _then_correlation(state: _World, cid: str) -> None:
    assert state.ok().events.cover.correlation_id == cid


@then("the command is refused because the aggregate has moved on")
def _then_refused_moved_on(state: _World) -> None:
    err = state.err()
    assert err.is_precondition_failed(), f"expected FAILED_PRECONDITION, got {err!r}"


@then("one command is accepted")
def _then_one_accepted(state: _World) -> None:
    accepted = [r for r in state.concurrent if isinstance(r, CommandResponse)]
    assert len(accepted) == 1, state.concurrent


@then("the other is refused because the aggregate has moved on")
def _then_other_refused(state: _World) -> None:
    refused = [r for r in state.concurrent if isinstance(r, ClientError)]
    assert len(refused) == 1, state.concurrent
    assert refused[0].is_precondition_failed(), refused[0]


@then("the response returns before any projectors have caught up")
def _then_returns_before_projectors(state: _World) -> None:
    resp = state.ok()
    assert len(resp.projections) == 0, "fire-and-forget response carried projections"
    assert state.backend.projector_runs == [], "projector ran before the response"
    assert state.backend.sync_modes[-1] == SyncMode.SYNC_MODE_ASYNC
    state.backend.drain()
    assert [run[2] for run in state.backend.projector_runs] == _seqs(resp.events)


@then("the response reflects the projectors having processed the event")
def _then_projectors_processed(state: _World) -> None:
    resp = state.ok()
    projected = [p.sequence for p in resp.projections]
    assert projected, "no projections in the response"
    assert projected == _seqs(resp.events)
    assert state.backend.sync_modes[-1] == SyncMode.SYNC_MODE_SIMPLE


@then("the response reflects the downstream sagas having completed")
def _then_sagas_completed(state: _World) -> None:
    state.ok()
    downstream = state.query_client().query("inventory", state.root).get_event_book()
    assert len(downstream.pages) == 1, "saga command not applied before return"
    assert short_type(downstream.pages[0].event) == "StockReserved"
    assert state.backend.sync_modes[-1] == SyncMode.SYNC_MODE_CASCADE


@then("the command is refused as invalid")
def _then_refused_invalid(state: _World) -> None:
    err = state.err()
    assert err.is_invalid_argument(), f"expected INVALID_ARGUMENT, got {err!r}"


@then("the refusal names the missing field")
def _then_names_field(state: _World) -> None:
    err = state.err()
    assert isinstance(err, GRPCError)
    assert "customer_id" in err.grpc_details, err.grpc_details


@then("the command is refused because the domain is unknown")
def _then_unknown_domain(state: _World) -> None:
    err = state.err()
    assert err.is_not_found(), f"expected NOT_FOUND, got {err!r}"


@then(parsers.parse("{n:d} events are recorded"))
def _then_n_recorded(state: _World, n: int) -> None:
    book = state.ok().events
    assert len(book.pages) == n
    assert len(state.backend.stored_pages(book.cover)) == n


@then(parsers.parse("the events occupy consecutive sequences starting at {start:d}"))
def _then_consecutive(state: _World, start: int) -> None:
    seqs = _seqs(state.ok().events)
    assert seqs == list(range(start, start + len(seqs)))


@then(parsers.parse("either all {n:d} events are present or none of them are"))
def _then_atomic(state: _World, n: int) -> None:
    assert state.read_back is not None
    read = len(state.read_back.pages)
    assert read in (n, 0), f"partial write: {read} of {n}"


@then("the call fails because the service cannot be reached")
def _then_unreachable(state: _World) -> None:
    err = state.err()
    assert err.is_connection_error(), f"expected connection error, got {err!r}"


@then("the call fails because the deadline was exceeded")
def _then_deadline(state: _World) -> None:
    err = state.err()
    assert isinstance(err, GRPCError)
    assert err.grpc_code == grpc.StatusCode.DEADLINE_EXCEEDED


@then("the aggregate now exists with one event")
def _then_exists_one(state: _World) -> None:
    book = state.query_client().query(state.domain, state.root).get_event_book()
    assert len(book.pages) == 1
    assert book.next_sequence == 1
