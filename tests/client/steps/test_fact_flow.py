"""Step defs for features/coordinator-contract/fact_flow.feature.

EXPLICIT SIMULATION — finding #28 in PARITY_AUDIT.md. The feature
describes coordinator-side fact persistence (sequence assignment,
external_id idempotency, failure rollback) which has no client
production code to call. The mock dataclasses here are explicit
stand-ins; the cucumber prose is the canonical contract. Kept
running so the prose doesn't bit-rot pending a coordinator-tier
suite.
"""

from __future__ import annotations

import uuid as _uuid
from dataclasses import dataclass, field

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

scenarios("../coordinator-contract/fact_flow.feature")


@dataclass
class _MockEvent:
    event_type: str


@dataclass
class _MockAggregate:
    root_id: _uuid.UUID = field(default_factory=_uuid.uuid4)
    events: list[_MockEvent] = field(default_factory=list)
    next_sequence: int = 0


@dataclass
class _MockFact:
    domain: str = ""
    root_id: _uuid.UUID = field(default_factory=_uuid.uuid4)
    external_id: str = ""
    correlation_id: str = ""


@dataclass
class _MockSaga:
    emitted_facts: list[_MockFact] = field(default_factory=list)
    error: str | None = None
    target_domain: str = ""


@dataclass
class _State:
    player_name: str = ""
    player_aggregate: _MockAggregate | None = None
    table_aggregate: _MockAggregate | None = None
    hand_aggregate: _MockAggregate | None = None
    hand_in_progress: bool = False
    turn_change_processed: bool = False
    fact_injected: _MockFact | None = None
    fact_sequence: int | None = None
    saga: _MockSaga | None = None
    error: str | None = None
    events_stored: int = 0
    external_id: str | None = None


@pytest.fixture
def state() -> _State:
    return _State()


# --- Given ------------------------------------------------------------------


@given(parsers.parse('a registered player "{name}"'))
def _given_registered_player(state: _State, name: str) -> None:
    state.player_name = name
    state.player_aggregate = _MockAggregate(
        events=[_MockEvent(event_type="PlayerRegistered")], next_sequence=1
    )


@given(parsers.parse("a player aggregate with {count:d} existing events"))
def _given_player_with_events(state: _State, count: int) -> None:
    state.player_aggregate = _MockAggregate(
        events=[_MockEvent(event_type="SomeEvent") for _ in range(count)],
        next_sequence=count + 1,
    )


@given(parsers.re(r"a hand in progress where it becomes (?P<name>\S+)'s turn"))
def _given_hand_in_progress(state: _State, name: str) -> None:
    state.player_name = name
    state.hand_in_progress = True
    state.hand_aggregate = _MockAggregate(
        events=[
            _MockEvent(event_type="HandStarted"),
            _MockEvent(event_type="TurnChanged"),
        ],
        next_sequence=2,
    )


@given(parsers.parse('player "{name}" is seated at table "{table}"'))
def _given_player_seated(state: _State, name: str, table: str) -> None:
    state.player_name = name
    state.table_aggregate = _MockAggregate(
        root_id=_uuid.UUID(int=0),
        events=[_MockEvent(event_type="PlayerSeated")],
        next_sequence=1,
    )


@given(parsers.parse('player "{name}" is sitting out at table "{table}"'))
def _given_player_sitting_out(state: _State, name: str, table: str) -> None:
    state.player_name = name
    state.table_aggregate = _MockAggregate(
        root_id=_uuid.UUID(int=0),
        events=[
            _MockEvent(event_type="PlayerSeated"),
            _MockEvent(event_type="PlayerSatOut"),
        ],
        next_sequence=2,
    )


@given("a saga that emits a fact")
def _given_saga_emits(state: _State) -> None:
    state.saga = _MockSaga(target_domain="test")


@given(parsers.parse('a saga that emits a fact to domain "{domain}"'))
def _given_saga_emits_to(state: _State, domain: str) -> None:
    state.saga = _MockSaga(target_domain=domain)


@given(parsers.parse('a fact with external_id "{ext_id}"'))
def _given_fact_with_external_id(state: _State, ext_id: str) -> None:
    state.external_id = ext_id
    state.saga = _MockSaga()


# --- When -------------------------------------------------------------------


@when("the hand-player saga processes the turn change")
def _when_hand_saga_processes(state: _State) -> None:
    state.turn_change_processed = True
    if state.saga is None:
        state.saga = _MockSaga(target_domain="player")
    if state.player_aggregate is not None:
        fact = _MockFact(
            domain="player",
            root_id=state.player_aggregate.root_id,
            external_id=f"action-H1-{state.player_name}-turn-1",
            correlation_id=str(_uuid.uuid4()),
        )
        state.fact_sequence = state.player_aggregate.next_sequence
        state.fact_injected = fact
        state.saga.emitted_facts.append(fact)


@when("an ActionRequested fact is injected")
def _when_action_requested_injected(state: _State) -> None:
    if state.player_aggregate is None:
        state.player_aggregate = _MockAggregate()
    agg = state.player_aggregate
    next_seq = agg.next_sequence
    agg.events.append(_MockEvent(event_type="ActionRequested"))
    agg.next_sequence += 1
    state.fact_sequence = next_seq
    state.fact_injected = _MockFact(
        domain="player",
        root_id=agg.root_id,
        external_id="fact-1",
        correlation_id=str(_uuid.uuid4()),
    )


@when(parsers.re(r"(?P<player_name>\S+)'s player aggregate emits PlayerSittingOut"))
def _when_player_emits_sitting_out(state: _State, player_name: str) -> None:
    if state.table_aggregate is not None:
        state.fact_sequence = state.table_aggregate.next_sequence
        state.table_aggregate.events.append(_MockEvent(event_type="PlayerSatOut"))
        state.table_aggregate.next_sequence += 1
        state.fact_injected = _MockFact(
            domain="table",
            root_id=state.table_aggregate.root_id,
            external_id="fact-1",
            correlation_id=str(_uuid.uuid4()),
        )


@when(parsers.re(r"(?P<player_name>\S+)'s player aggregate emits PlayerReturning"))
def _when_player_emits_returning(state: _State, player_name: str) -> None:
    if state.table_aggregate is not None:
        state.fact_sequence = state.table_aggregate.next_sequence
        state.table_aggregate.events.append(_MockEvent(event_type="PlayerSatIn"))
        state.table_aggregate.next_sequence += 1
        state.fact_injected = _MockFact(
            domain="table",
            root_id=state.table_aggregate.root_id,
            external_id="fact-1",
            correlation_id=str(_uuid.uuid4()),
        )


@when("the fact is constructed")
def _when_fact_constructed(state: _State) -> None:
    if state.saga is None:
        state.saga = _MockSaga()
    fact = _MockFact(
        domain="player",
        external_id=str(_uuid.uuid4()),
        correlation_id=str(_uuid.uuid4()),
    )
    state.saga.emitted_facts.append(fact)
    state.fact_injected = fact


@when("the saga processes an event")
def _when_saga_processes(state: _State) -> None:
    if state.saga is None:
        return
    if state.saga.target_domain == "nonexistent":
        state.saga.error = "Domain not found"
        state.error = "Domain not found"
    else:
        state.saga.emitted_facts.append(
            _MockFact(domain=state.saga.target_domain, external_id="fact-1")
        )


@when("the same fact is injected twice")
def _when_same_fact_twice(state: _State) -> None:
    state.events_stored = 1  # idempotent


# --- Then -------------------------------------------------------------------


@then(
    parsers.re(
        r"an ActionRequested fact is injected into (?P<player_name>\S+)'s player aggregate"
    )
)
def _then_action_requested_injected(state: _State, player_name: str) -> None:
    assert state.fact_injected is not None


@then("the fact is appended at the next sequence number")
def _then_fact_next_seq(state: _State) -> None:
    assert state.fact_sequence is not None


@then(
    parsers.re(
        r"(?P<player_name>\S+)'s player aggregate records the " r"ActionRequested fact"
    )
)
def _then_player_records_fact(state: _State, player_name: str) -> None:
    assert state.player_aggregate is not None
    assert len(state.player_aggregate.events) > 0


@then(parsers.parse("the fact is persisted with sequence number {seq:d}"))
def _then_fact_at_seq(state: _State, seq: int) -> None:
    assert state.fact_sequence == seq


@then(parsers.parse("subsequent events continue from sequence {seq:d}"))
def _then_continue_from(state: _State, seq: int) -> None:
    assert state.player_aggregate is not None
    assert state.player_aggregate.next_sequence == seq


@then("a PlayerSatOut fact is injected into the table aggregate")
def _then_sat_out(state: _State) -> None:
    assert state.fact_injected is not None
    assert state.fact_injected.domain == "table"


@then(parsers.re(r"the table records (?P<player_name>\S+) as sitting out"))
def _then_table_sitting_out(state: _State, player_name: str) -> None:
    assert state.table_aggregate is not None


@then("the fact has a sequence number in the table's event stream")
def _then_fact_has_seq(state: _State) -> None:
    assert state.fact_sequence is not None


@then("a PlayerSatIn fact is injected into the table aggregate")
def _then_sat_in(state: _State) -> None:
    assert state.fact_injected is not None
    assert state.fact_injected.domain == "table"


@then(parsers.re(r"the table records (?P<player_name>\S+) as active"))
def _then_table_active(state: _State, player_name: str) -> None:
    assert state.table_aggregate is not None


@then("the fact Cover has domain set to the target aggregate")
def _then_cover_domain(state: _State) -> None:
    assert state.fact_injected is not None
    assert state.fact_injected.domain != ""


@then("the fact Cover has root set to the target aggregate root")
def _then_cover_root(state: _State) -> None:
    assert state.fact_injected is not None


@then("the fact Cover has external_id set for idempotency")
def _then_cover_external_id(state: _State) -> None:
    assert state.fact_injected is not None
    assert state.fact_injected.external_id != ""


@then("the fact Cover has correlation_id for traceability")
def _then_cover_correlation_id(state: _State) -> None:
    assert state.fact_injected is not None
    assert state.fact_injected.correlation_id != ""


@then("the saga fails because the target domain does not exist")
def _then_saga_fails_missing_domain(state: _State) -> None:
    """Combined business-rejection assertion.

    Replaces ``the saga fails with error containing "not found"`` —
    business prose says *why* the saga fails (target domain absent)
    rather than asserting on the literal error string.
    """
    assert state.error is not None
    assert "not found" in state.error.lower()


@then("no commands from that saga are executed")
def _then_no_commands(state: _State) -> None:
    assert state.saga is not None
    assert state.saga.error is not None


@then("only one event is stored in the aggregate")
def _then_one_event(state: _State) -> None:
    assert state.events_stored == 1


@then("the second injection succeeds without error")
def _then_second_ok(state: _State) -> None:
    assert state.error is None
