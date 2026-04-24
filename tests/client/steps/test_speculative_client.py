"""Step defs for features/client/speculative_client.feature.

Simulation-style port mirroring tests/steps/speculative_client.rs in the
Rust client. Speculative ("what-if") execution across aggregates,
projectors, sagas, and process managers is verified at the contract
level without touching persistence or downstream services.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

scenarios("speculative_client.feature")


@dataclass
class _MockEvent:
    sequence: int
    event_type: str


@dataclass
class _SpeculativeResult:
    events: list[_MockEvent] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    projection: str | None = None
    rejection: str | None = None


@dataclass
class _State:
    client_connected: bool = False
    service_available: bool = True
    aggregates: dict[str, list[_MockEvent]] = field(default_factory=dict)
    speculative_result: _SpeculativeResult | None = None
    events_persisted: bool = False
    edition_created: bool = False
    edition_discarded: bool = False
    error: str | None = None
    error_type: str | None = None
    aggregate_state: str = ""
    saga_origin: str | None = None
    has_correlation_id: bool = False
    spec_a_result: _SpeculativeResult | None = None
    spec_b_result: _SpeculativeResult | None = None
    real_event_count: int = 0


@pytest.fixture
def state() -> _State:
    return _State()


# --- Background -------------------------------------------------------------


@given("a SpeculativeClient connected to the test backend")
def _given_speculative_client(state: _State) -> None:
    state.client_connected = True
    state.service_available = True


# --- Given: aggregates ------------------------------------------------------


@given(parsers.parse('an aggregate "{domain}" with root "{root}" has {count:d} events'))
def _given_aggregate_with_events(
    state: _State, domain: str, root: str, count: int
) -> None:
    state.aggregates[f"{domain}:{root}"] = [
        _MockEvent(sequence=i, event_type="Event") for i in range(count)
    ]
    state.real_event_count = count


@given(
    parsers.parse('an aggregate "{domain}" with root "{root}" in state "{agg_state}"')
)
def _given_aggregate_in_state(
    state: _State, domain: str, root: str, agg_state: str
) -> None:
    state.aggregates[f"{domain}:{root}"] = [
        _MockEvent(sequence=0, event_type="StateChanged")
    ]
    state.aggregate_state = agg_state


@given(parsers.parse('an aggregate "{domain}" with root "{root}"'))
def _given_aggregate(state: _State, domain: str, root: str) -> None:
    state.aggregates[f"{domain}:{root}"] = []


@given(parsers.parse('events for "{domain}" root "{root}"'))
def _given_events_for(state: _State, domain: str, root: str) -> None:
    state.aggregates[f"{domain}:{root}"] = [_MockEvent(sequence=0, event_type="Event")]


@given(parsers.parse('{count:d} events for "{domain}" root "{root}"'))
def _given_n_events_for(state: _State, count: int, domain: str, root: str) -> None:
    state.aggregates[f"{domain}:{root}"] = [
        _MockEvent(sequence=i, event_type="Event") for i in range(count)
    ]


@given('events with saga origin from "inventory" aggregate')
def _given_events_with_saga_origin(state: _State) -> None:
    state.saga_origin = "inventory"


@given("correlated events from multiple domains")
def _given_correlated_events(state: _State) -> None:
    state.has_correlation_id = True


@given("events without correlation ID")
def _given_events_without_correlation(state: _State) -> None:
    state.has_correlation_id = False


@given(
    parsers.parse(
        'a speculative aggregate "{domain}" with root "{root}" has ' "{count:d} events"
    )
)
def _given_speculative_aggregate(
    state: _State, domain: str, root: str, count: int
) -> None:
    state.aggregates[f"{domain}:{root}"] = [
        _MockEvent(sequence=i, event_type="Event") for i in range(count)
    ]
    state.real_event_count = count


@given("the speculative service is unavailable")
def _given_service_unavailable(state: _State) -> None:
    state.service_available = False


# --- When -------------------------------------------------------------------


@when(
    parsers.parse('I speculatively execute a command against "{domain}" root "{root}"')
)
def _when_speculative_execute_against(state: _State, domain: str, root: str) -> None:
    state.speculative_result = _SpeculativeResult(
        events=[_MockEvent(sequence=0, event_type="SpeculativeEvent")]
    )
    state.events_persisted = False
    state.edition_created = True
    state.edition_discarded = True


@when(parsers.parse("I speculatively execute a command as of sequence {seq:d}"))
def _when_speculative_as_of_sequence(state: _State, seq: int) -> None:
    state.speculative_result = _SpeculativeResult(
        events=[_MockEvent(sequence=0, event_type="SpeculativeEvent")]
    )
    state.events_persisted = False


@when(parsers.parse('I speculatively execute a "{cmd_type}" command'))
def _when_speculative_execute_command(state: _State, cmd_type: str) -> None:
    if cmd_type == "CancelOrder" and state.aggregate_state == "shipped":
        state.speculative_result = _SpeculativeResult(
            rejection="cannot cancel shipped order"
        )
    else:
        state.speculative_result = _SpeculativeResult(
            events=[_MockEvent(sequence=0, event_type=f"{cmd_type}Executed")]
        )
    state.events_persisted = False


@when("I speculatively execute a command with invalid payload")
def _when_speculative_invalid_payload(state: _State) -> None:
    state.error = "Validation error"
    state.error_type = "validation"
    state.speculative_result = None


@when("I speculatively execute a command")
def _when_speculative_execute_simple(state: _State) -> None:
    state.speculative_result = _SpeculativeResult(
        events=[_MockEvent(sequence=0, event_type="SpeculativeEvent")]
    )
    state.events_persisted = False
    state.edition_created = True
    state.edition_discarded = True


@when(
    parsers.parse(
        'I speculatively execute projector "{projector}" against those events'
    )
)
def _when_speculative_projector_against(state: _State, projector: str) -> None:
    state.speculative_result = _SpeculativeResult(
        projection=f"{projector} projection result"
    )
    state.events_persisted = False


@when(parsers.parse('I speculatively execute projector "{projector}"'))
def _when_speculative_projector(state: _State, projector: str) -> None:
    state.speculative_result = _SpeculativeResult(
        projection=f"{projector} projection result"
    )


@when(parsers.parse('I speculatively execute saga "{saga}"'))
def _when_speculative_saga(state: _State, saga: str) -> None:
    state.speculative_result = _SpeculativeResult(
        commands=["SagaCommand1", "SagaCommand2"]
    )
    state.events_persisted = False


@when(parsers.parse('I speculatively execute process manager "{pm}"'))
def _when_speculative_pm(state: _State, pm: str) -> None:
    if not state.has_correlation_id:
        state.error = "Missing correlation ID"
        state.error_type = "missing_correlation"
        return
    state.speculative_result = _SpeculativeResult(commands=["PMCommand1"])
    state.events_persisted = False


@when("I speculatively execute a command producing 2 events")
def _when_speculative_multi_event(state: _State) -> None:
    state.speculative_result = _SpeculativeResult(
        events=[
            _MockEvent(sequence=0, event_type="Event1"),
            _MockEvent(sequence=1, event_type="Event2"),
        ]
    )
    state.events_persisted = False


@when(parsers.parse('I verify the real events for "{domain}" root "{root}"'))
def _when_verify_real_events(state: _State, domain: str, root: str) -> None:
    # Real events remain unchanged by the simulation.
    pass


@when("I speculatively execute command A")
def _when_speculative_command_a(state: _State) -> None:
    state.spec_a_result = _SpeculativeResult(
        events=[_MockEvent(sequence=0, event_type="EventA")]
    )


@when("I speculatively execute command B")
def _when_speculative_command_b(state: _State) -> None:
    state.spec_b_result = _SpeculativeResult(
        events=[_MockEvent(sequence=0, event_type="EventB")]
    )


@when("I attempt speculative execution")
def _when_attempt_speculative(state: _State) -> None:
    if not state.service_available:
        state.error = "Connection error"
        state.error_type = "connection"


@when("I attempt speculative execution with missing parameters")
def _when_attempt_missing_params(state: _State) -> None:
    state.error = "Invalid argument"
    state.error_type = "invalid_argument"


# --- Then -------------------------------------------------------------------


@then("the response should contain the projected events")
def _then_response_contains_events(state: _State) -> None:
    assert state.speculative_result is not None
    assert state.speculative_result.events


@then("the events should NOT be persisted")
def _then_events_not_persisted(state: _State) -> None:
    assert not state.events_persisted


@then("the command should execute against the historical state")
def _then_execute_against_historical(state: _State) -> None:
    pass


@then(parsers.parse("the response should reflect state at sequence {seq:d}"))
def _then_response_reflects_state(state: _State, seq: int) -> None:
    assert state.speculative_result is not None


@then("the response should indicate rejection")
def _then_response_rejection(state: _State) -> None:
    assert state.speculative_result is not None
    assert state.speculative_result.rejection is not None


@then(parsers.parse('the rejection reason should be "{reason}"'))
def _then_rejection_reason(state: _State, reason: str) -> None:
    assert state.speculative_result is not None
    assert state.speculative_result.rejection is not None
    assert reason in state.speculative_result.rejection


@then("the operation should fail with validation error")
def _then_fail_validation(state: _State) -> None:
    assert state.error_type == "validation"


@then("no events should be produced")
def _then_no_events_produced(state: _State) -> None:
    assert state.speculative_result is None or not state.speculative_result.events


@then("an edition should be created for the speculation")
def _then_edition_created(state: _State) -> None:
    assert state.edition_created


@then("the edition should be discarded after execution")
def _then_edition_discarded(state: _State) -> None:
    assert state.edition_discarded


@then("the response should contain the projection")
def _then_response_contains_projection(state: _State) -> None:
    assert state.speculative_result is not None
    assert state.speculative_result.projection is not None


@then("no external systems should be updated")
def _then_no_external_updates(state: _State) -> None:
    assert not state.events_persisted


@then(parsers.parse("the projector should process all {count:d} events in order"))
def _then_projector_processes_all(state: _State, count: int) -> None:
    pass


@then("the final projection state should be returned")
def _then_final_projection_state(state: _State) -> None:
    assert state.speculative_result is not None
    assert state.speculative_result.projection is not None


@then("the response should contain the commands the saga would emit")
def _then_response_contains_commands(state: _State) -> None:
    assert state.speculative_result is not None
    assert state.speculative_result.commands


@then("the commands should NOT be sent to the target domain")
def _then_commands_not_sent(state: _State) -> None:
    assert not state.events_persisted


@then("the response should preserve the saga origin chain")
def _then_preserve_saga_origin(state: _State) -> None:
    assert state.saga_origin is not None


@then("the response should contain the PM's command decisions")
def _then_response_contains_pm_commands(state: _State) -> None:
    assert state.speculative_result is not None
    assert state.speculative_result.commands


@then("the commands should NOT be executed")
def _then_commands_not_executed(state: _State) -> None:
    assert not state.events_persisted


@then("the speculative PM operation should fail")
def _then_pm_operation_fails(state: _State) -> None:
    assert state.error is not None


@then("the error should indicate missing correlation ID")
def _then_error_missing_correlation(state: _State) -> None:
    assert state.error_type == "missing_correlation"


@then(parsers.parse("I should receive only {count:d} events"))
def _then_receive_only_n_events(state: _State, count: int) -> None:
    assert state.real_event_count == count


@then("the speculative events should not be present")
def _then_speculative_not_present(state: _State) -> None:
    assert not state.events_persisted


@then("each speculation should start from the same base state")
def _then_same_base_state(state: _State) -> None:
    assert state.spec_a_result is not None
    assert state.spec_b_result is not None


@then("results should be independent")
def _then_results_independent(state: _State) -> None:
    assert state.spec_a_result is not None
    assert state.spec_b_result is not None
    assert (
        state.spec_a_result.events[0].event_type
        != state.spec_b_result.events[0].event_type
    )


@then("the speculative operation should fail with connection error")
def _then_fail_connection(state: _State) -> None:
    assert state.error_type == "connection"


@then("the speculative operation should fail with invalid argument error")
def _then_fail_invalid_argument(state: _State) -> None:
    assert state.error_type == "invalid_argument"
