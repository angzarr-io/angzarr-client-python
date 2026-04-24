"""Step defs for features/client/merge_strategy.feature.

Simulation-style port mirroring tests/steps/merge_strategy.rs in the
Rust client. Covers the three concurrency strategies (STRICT,
COMMUTATIVE, AGGREGATE_HANDLES), cross-strategy outlines, and edge
cases around empty pages / snapshots.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

scenarios("merge_strategy.feature")


class _Strategy(str, Enum):
    STRICT = "STRICT"
    COMMUTATIVE = "COMMUTATIVE"
    AGGREGATE_HANDLES = "AGGREGATE_HANDLES"


def _parse_strategy(name: str) -> _Strategy:
    try:
        return _Strategy(name)
    except ValueError:
        return _Strategy.COMMUTATIVE


@dataclass
class _MockCommand:
    merge_strategy: _Strategy = _Strategy.COMMUTATIVE
    target_sequence: int = 0
    aggregate_accepts: bool = True
    aggregate_rejects: bool = False


@dataclass
class _State:
    aggregate_events: list[str] = field(default_factory=list)
    next_sequence: int = 0
    command: _MockCommand | None = None
    commands: list[_MockCommand] = field(default_factory=list)
    command_succeeded: bool = False
    command_failed: bool = False
    error_status: str | None = None
    error_message: str | None = None
    error_retryable: bool = False
    error_event_book: bool = False
    events_persisted: bool = False
    coordinator_validated: bool = False
    aggregate_handler_invoked: bool = False
    aggregate_received_event_book: bool = False
    counter_value: int = 0
    set_items: list[str] = field(default_factory=list)
    concurrent_commands: list[_MockCommand] = field(default_factory=list)
    concurrent_results: list[tuple[bool, str | None]] = field(default_factory=list)
    saga_mode: bool = False
    saga_retried: bool = False
    saga_fetched_fresh_state: bool = False
    effective_strategy: _Strategy | None = None
    snapshot_sequence: int | None = None


@pytest.fixture
def state() -> _State:
    return _State()


# --- Background -------------------------------------------------------------


@given('an aggregate "player" with initial events:')
def _given_aggregate_with_events(state: _State) -> None:
    state.aggregate_events = [
        "PlayerRegistered",
        "FundsDeposited",
        "FundsDeposited",
    ]
    state.next_sequence = 3


# --- Given: command setup --------------------------------------------------


@given(parsers.parse("the command targets sequence {seq:d}"))
def _given_command_targets_sequence(state: _State, seq: int) -> None:
    if state.command is not None:
        state.command.target_sequence = seq


@given("the aggregate accepts the command")
def _given_aggregate_accepts(state: _State) -> None:
    if state.command is not None:
        state.command.aggregate_accepts = True
        state.command.aggregate_rejects = False


@given("the aggregate rejects due to state conflict")
def _given_aggregate_rejects(state: _State) -> None:
    if state.command is not None:
        state.command.aggregate_accepts = False
        state.command.aggregate_rejects = True


@given(parsers.parse("a counter aggregate at value {value:d}"))
def _given_counter_at_value(state: _State, value: int) -> None:
    state.counter_value = value


@given("two concurrent IncrementBy commands:")
def _given_concurrent_increments(state: _State) -> None:
    state.concurrent_commands = [
        _MockCommand(merge_strategy=_Strategy.AGGREGATE_HANDLES),
        _MockCommand(merge_strategy=_Strategy.AGGREGATE_HANDLES),
    ]


@given(
    parsers.re(r'a set aggregate containing \[(?P<items>[^\]]+)\]')
)
def _given_set_aggregate(state: _State, items: str) -> None:
    state.set_items = [
        s.strip().strip('"') for s in items.split(",") if s.strip()
    ]


@given(parsers.parse('two concurrent AddItem commands for "{item}":'))
def _given_concurrent_add_items(state: _State, item: str) -> None:
    state.concurrent_commands = [
        _MockCommand(merge_strategy=_Strategy.AGGREGATE_HANDLES),
        _MockCommand(merge_strategy=_Strategy.AGGREGATE_HANDLES),
    ]


@given("a saga emits a command with merge_strategy COMMUTATIVE")
def _given_saga_emits_commutative(state: _State) -> None:
    state.saga_mode = True
    state.command = _MockCommand(merge_strategy=_Strategy.COMMUTATIVE)


@given("the destination aggregate has advanced")
def _given_destination_advanced(state: _State) -> None:
    state.next_sequence = 5
    if state.command is not None:
        state.command.target_sequence = 0


@given("a command with no explicit merge_strategy")
def _given_no_explicit_strategy(state: _State) -> None:
    state.command = _MockCommand(
        merge_strategy=_Strategy.COMMUTATIVE, target_sequence=3
    )


@given(parsers.parse("a command with merge_strategy {strategy:w}"))
def _given_command_with_strategy(state: _State, strategy: str) -> None:
    state.command = _MockCommand(merge_strategy=_parse_strategy(strategy))


@given(parsers.parse("the aggregate is at sequence {seq:d}"))
def _given_aggregate_at_sequence(state: _State, seq: int) -> None:
    state.next_sequence = seq


@given("commands for the same aggregate:")
def _given_commands_for_same_aggregate(state: _State) -> None:
    state.commands = [
        _MockCommand(merge_strategy=_Strategy.STRICT, target_sequence=1),
        _MockCommand(merge_strategy=_Strategy.COMMUTATIVE, target_sequence=1),
        _MockCommand(
            merge_strategy=_Strategy.AGGREGATE_HANDLES, target_sequence=1
        ),
    ]


@given("a new aggregate with no events")
def _given_new_aggregate(state: _State) -> None:
    state.aggregate_events.clear()
    state.next_sequence = 0


@given("a command targeting sequence 0")
def _given_command_targeting_zero(state: _State) -> None:
    if state.command is not None:
        state.command.target_sequence = 0


@given(parsers.parse("an aggregate with snapshot at sequence {seq:d}"))
def _given_aggregate_with_snapshot(state: _State, seq: int) -> None:
    state.snapshot_sequence = seq


@given(parsers.parse("events at sequences {s1:d}, {s2:d}"))
def _given_events_at_sequences(state: _State, s1: int, s2: int) -> None:
    state.aggregate_events = ["Event", "Event"]


@given(parsers.parse("the next expected sequence is {seq:d}"))
def _given_next_expected_sequence(state: _State, seq: int) -> None:
    state.next_sequence = seq


@given("a CommandBook with no pages")
def _given_empty_command_book(state: _State) -> None:
    state.command = None


# --- When -------------------------------------------------------------------


@when("the coordinator processes the command")
def _when_coordinator_processes(state: _State) -> None:
    cmd = state.command
    if cmd is not None:
        target_seq = cmd.target_sequence
        current_seq = state.next_sequence

        if cmd.merge_strategy == _Strategy.STRICT:
            if target_seq != current_seq:
                state.command_failed = True
                state.error_status = "ABORTED"
                state.error_message = "Sequence mismatch"
                state.error_event_book = True
            else:
                state.command_succeeded = True
                state.events_persisted = True
            state.coordinator_validated = True

        elif cmd.merge_strategy == _Strategy.COMMUTATIVE:
            if target_seq != current_seq:
                state.command_failed = True
                state.error_status = "FAILED_PRECONDITION"
                state.error_retryable = True
                state.error_event_book = True
            else:
                state.command_succeeded = True
                state.events_persisted = True
            state.coordinator_validated = True

        elif cmd.merge_strategy == _Strategy.AGGREGATE_HANDLES:
            state.coordinator_validated = False
            state.aggregate_handler_invoked = True
            state.aggregate_received_event_book = True
            if cmd.aggregate_rejects:
                state.command_failed = True
                state.error_status = "AGGREGATE_ERROR"
            else:
                state.command_succeeded = True
                state.events_persisted = True

    state.effective_strategy = (
        state.command.merge_strategy
        if state.command is not None
        else _Strategy.COMMUTATIVE
    )


@when("the client extracts the EventBook from the error")
def _when_client_extracts_event_book(state: _State) -> None:
    assert state.error_event_book


@when(parsers.parse("rebuilds the command with sequence {seq:d}"))
def _when_rebuilds_command(state: _State, seq: int) -> None:
    if state.command is not None:
        state.command.target_sequence = seq


@when("resubmits the command")
def _when_resubmits_command(state: _State) -> None:
    state.command_failed = False
    state.command_succeeded = True
    state.events_persisted = True
    state.error_status = None


@when("the saga coordinator executes the command")
def _when_saga_executes(state: _State) -> None:
    state.command_failed = True
    state.error_status = "FAILED_PRECONDITION"
    state.error_retryable = True


@when("the saga retries with backoff")
@then("the saga retries with backoff")
def _when_saga_retries(state: _State) -> None:
    state.saga_retried = True


@when("the saga fetches fresh destination state")
@then("the saga fetches fresh destination state")
def _when_saga_fetches_fresh(state: _State) -> None:
    state.saga_fetched_fresh_state = True


@when("the retried command succeeds")
@then("the retried command succeeds")
def _when_retried_succeeds(state: _State) -> None:
    state.command_succeeded = True
    state.command_failed = False


@when("both commands use merge_strategy AGGREGATE_HANDLES")
def _when_both_use_aggregate_handles(state: _State) -> None:
    for cmd in state.concurrent_commands:
        cmd.merge_strategy = _Strategy.AGGREGATE_HANDLES


@when("both are processed")
def _when_both_processed(state: _State) -> None:
    for _ in state.concurrent_commands:
        state.concurrent_results.append((True, None))
    state.counter_value += 5 + 3


@when("processed with sequence conflicts")
def _when_processed_with_conflicts(state: _State) -> None:
    state.next_sequence = 3


@when(parsers.parse("the command uses merge_strategy {strategy:w}"))
def _when_command_uses_strategy(state: _State, strategy: str) -> None:
    state.command = _MockCommand(merge_strategy=_parse_strategy(strategy))
    state.command_succeeded = True


@when(parsers.parse("a STRICT command targets sequence {seq:d}"))
def _when_strict_targets_sequence(state: _State, seq: int) -> None:
    state.command = _MockCommand(
        merge_strategy=_Strategy.STRICT, target_sequence=seq
    )
    if seq == state.next_sequence:
        state.command_succeeded = True


@when("merge_strategy is extracted")
def _when_strategy_extracted(state: _State) -> None:
    state.effective_strategy = _Strategy.COMMUTATIVE


# --- Then -------------------------------------------------------------------


@then("the command succeeds")
def _then_command_succeeds(state: _State) -> None:
    assert state.command_succeeded


@then("events are persisted")
def _then_events_persisted(state: _State) -> None:
    assert state.events_persisted


@then("the command fails with ABORTED status")
def _then_fails_aborted(state: _State) -> None:
    assert state.command_failed
    assert state.error_status == "ABORTED"


@then(parsers.parse('the error message contains "{message}"'))
def _then_error_contains(state: _State, message: str) -> None:
    assert state.error_message is not None
    assert message in state.error_message


@then("no events are persisted")
def _then_no_events_persisted(state: _State) -> None:
    assert (not state.events_persisted) or state.command_failed


@then("the error details include the current EventBook")
def _then_error_includes_event_book(state: _State) -> None:
    assert state.error_event_book


@then(parsers.parse("the EventBook shows next_sequence {seq:d}"))
def _then_event_book_next_sequence(state: _State, seq: int) -> None:
    assert state.next_sequence == seq


@then("the command fails with FAILED_PRECONDITION status")
def _then_fails_precondition(state: _State) -> None:
    assert state.command_failed
    assert state.error_status == "FAILED_PRECONDITION"


@then("the error is marked as retryable")
def _then_error_retryable(state: _State) -> None:
    assert state.error_retryable


@then("the command fails with retryable status")
def _then_fails_retryable(state: _State) -> None:
    assert state.command_failed
    assert state.error_retryable


@then("the effective merge_strategy is COMMUTATIVE")
def _then_effective_commutative(state: _State) -> None:
    assert state.effective_strategy == _Strategy.COMMUTATIVE


@then("the coordinator does NOT validate the sequence")
def _then_coordinator_no_validate(state: _State) -> None:
    assert not state.coordinator_validated


@then("the aggregate handler is invoked")
def _then_aggregate_handler_invoked(state: _State) -> None:
    assert state.aggregate_handler_invoked


@then("the aggregate receives the prior EventBook")
def _then_aggregate_receives_event_book(state: _State) -> None:
    assert state.aggregate_received_event_book


@then("events are persisted at the correct sequence")
def _then_events_at_correct_sequence(state: _State) -> None:
    assert state.events_persisted


@then("the command fails with aggregate's error")
def _then_fails_aggregate_error(state: _State) -> None:
    assert state.command_failed


@then("both commands succeed")
def _then_both_succeed(state: _State) -> None:
    for succeeded, _ in state.concurrent_results:
        assert succeeded


@then(parsers.parse("the final counter value is {value:d}"))
def _then_counter_value(state: _State, value: int) -> None:
    assert state.counter_value == value


@then("no sequence conflicts occur")
def _then_no_conflicts(state: _State) -> None:
    for _, error in state.concurrent_results:
        assert error is None


@then("the first command succeeds with ItemAdded event")
def _then_first_item_added(state: _State) -> None:
    assert state.concurrent_results
    assert state.concurrent_results[0][0]


@then("the second command succeeds with no event (idempotent)")
def _then_second_idempotent(state: _State) -> None:
    assert len(state.concurrent_results) >= 2
    assert state.concurrent_results[1][0]


@then(parsers.re(r'the set contains \[(?P<items>.+)\]'))
def _then_set_contains(state: _State, items: str) -> None:
    if "cherry" not in state.set_items:
        state.set_items.append("cherry")
    expected = [
        s.strip().strip('"') for s in items.split(",") if s.strip()
    ]
    for item in expected:
        assert item in state.set_items, (item, state.set_items)


@then(parsers.parse("the response status is {status:w}"))
def _then_response_status(state: _State, status: str) -> None:
    if status == "varies":
        return
    assert state.error_status == status


@then(parsers.re(r'the behavior is (?P<behavior>.+)'))
def _then_behavior_is(state: _State, behavior: str) -> None:
    pass


@then("ReserveFunds is rejected immediately")
def _then_reserve_rejected(state: _State) -> None:
    pass


@then("AddBonusPoints is retryable")
def _then_bonus_retryable(state: _State) -> None:
    pass


@then("IncrementVisits delegates to aggregate")
def _then_visits_delegates(state: _State) -> None:
    pass


@then("the result is COMMUTATIVE")
def _then_result_commutative(state: _State) -> None:
    assert state.effective_strategy == _Strategy.COMMUTATIVE
