"""Step defs for features/client/aggregate_client.feature.

Simulation-style port mirroring tests/steps/aggregate_client.rs in the
Rust client. The real angzarr_client.client.CommandHandlerClient is
covered by unit tests; this BDD tier pins the cross-language contract
shape for command execution, optimistic concurrency, sync modes, and
error handling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

scenarios("aggregate_client.feature")


@dataclass
class _State:
    service_available: bool = True
    service_slow: bool = False
    projectors_configured: bool = False
    sagas_configured: bool = False
    domain: str = ""
    root: str = ""
    sequence: int = 0
    command_type: str = ""
    command_data: str = ""
    correlation_id: str | None = None
    sync_mode: str = ""
    timeout_ms: int | None = None
    command_succeeded: bool = False
    command_failed: bool = False
    error: str | None = None
    error_type: str | None = None
    events_returned: list[tuple[str, int]] = field(default_factory=list)
    concurrent_results: list[bool] = field(default_factory=list)
    aggregates: dict[str, int] = field(default_factory=dict)
    current_sequence: int | None = None


@pytest.fixture
def state() -> _State:
    return _State()


# --- Background -------------------------------------------------------------


@given("an AggregateClient connected to the test backend")
def _given_aggregate_client(state: _State) -> None:
    state.service_available = True


# --- Given: aggregates ------------------------------------------------------


@given(parsers.parse('a new aggregate root in domain "{domain}"'))
def _given_new_aggregate(state: _State, domain: str) -> None:
    state.domain = domain
    state.root = str(uuid4())
    state.sequence = 0
    state.aggregates[f"{domain}:{state.root}"] = 0


@given(parsers.parse('an aggregate "{domain}" with root "{root}" at sequence {seq:d}'))
def _given_aggregate_at_sequence(
    state: _State, domain: str, root: str, seq: int
) -> None:
    state.domain = domain
    state.root = root
    state.sequence = seq
    state.aggregates[f"{domain}:{root}"] = seq


@given(parsers.parse('an aggregate "{domain}" with root "{root}"'))
def _given_aggregate(state: _State, domain: str, root: str) -> None:
    state.domain = domain
    state.root = root
    state.sequence = 0
    state.aggregates[f"{domain}:{root}"] = 0


@given(parsers.parse('no aggregate exists for domain "{domain}" root "{root}"'))
def _given_no_aggregate(state: _State, domain: str, root: str) -> None:
    state.domain = domain
    state.root = root
    state.sequence = 0


@given(parsers.parse('projectors are configured for "{domain}" domain'))
def _given_projectors_configured(state: _State, domain: str) -> None:
    state.projectors_configured = True


@given(parsers.parse('sagas are configured for "{domain}" domain'))
def _given_sagas_configured(state: _State, domain: str) -> None:
    state.sagas_configured = True


@given("the aggregate service is unavailable")
def _given_service_unavailable(state: _State) -> None:
    state.service_available = False


@given("the aggregate service is slow to respond")
def _given_service_slow(state: _State) -> None:
    state.service_slow = True


# --- When: commands ---------------------------------------------------------


@when(parsers.parse('I execute a "{cmd_type}" command with data "{data}"'))
def _when_execute_command_with_data(state: _State, cmd_type: str, data: str) -> None:
    state.command_type = cmd_type
    state.command_data = data
    state.command_succeeded = True
    if cmd_type.startswith("Create"):
        event_type = f"{cmd_type.removeprefix('Create')}Created"
    else:
        event_type = cmd_type
    state.events_returned.append((event_type, state.sequence))


@when(parsers.parse('I execute a "{cmd_type}" command at sequence {seq:d}'))
def _when_execute_named_at_sequence(state: _State, cmd_type: str, seq: int) -> None:
    state.command_type = cmd_type
    key = f"{state.domain}:{state.root}"
    current_seq = state.aggregates.get(key, 0)
    if seq != current_seq:
        state.command_failed = True
        state.error_type = "precondition"
        state.error = "Sequence mismatch"
    else:
        state.command_succeeded = True
        state.events_returned.append((cmd_type, seq))


@when(parsers.parse("I execute a command at sequence {seq:d}"))
def _when_execute_at_sequence(state: _State, seq: int) -> None:
    key = f"{state.domain}:{state.root}"
    current_seq = state.aggregates.get(key, 0)
    if seq != current_seq:
        state.command_failed = True
        state.error_type = "precondition"
        state.error = "Sequence mismatch"
    else:
        state.command_succeeded = True
        state.events_returned.append(("Event", seq))


@when(parsers.parse('I execute a command with correlation ID "{cid}"'))
def _when_execute_with_correlation(state: _State, cid: str) -> None:
    state.correlation_id = cid
    state.command_succeeded = True
    state.events_returned.append(("Event", state.sequence))


@when("two commands are sent concurrently at sequence 0")
def _when_concurrent_commands(state: _State) -> None:
    state.concurrent_results.append(True)
    state.concurrent_results.append(False)


@when(parsers.parse('I query the current sequence for "{domain}" root "{root}"'))
def _when_query_current_sequence(state: _State, domain: str, root: str) -> None:
    state.current_sequence = state.aggregates.get(f"{domain}:{root}")


@when("I retry the command at the correct sequence")
def _when_retry_correct_sequence(state: _State) -> None:
    state.command_succeeded = True
    state.command_failed = False
    state.error = None
    state.error_type = None


@when("I execute a command asynchronously")
def _when_execute_async(state: _State) -> None:
    state.sync_mode = "ASYNC"
    state.command_succeeded = True


@when("I execute a command with sync mode SIMPLE")
def _when_execute_sync_simple(state: _State) -> None:
    state.sync_mode = "SIMPLE"
    state.command_succeeded = True


@when("I execute a command with sync mode CASCADE")
def _when_execute_sync_cascade(state: _State) -> None:
    state.sync_mode = "CASCADE"
    state.command_succeeded = True


@when("I execute a command with malformed payload")
def _when_execute_malformed(state: _State) -> None:
    state.command_failed = True
    state.error_type = "invalid_argument"
    state.error = "Invalid payload"


@when("I execute a command without required fields")
def _when_execute_missing_fields(state: _State) -> None:
    state.command_failed = True
    state.error_type = "invalid_argument"
    state.error = "Missing required field: order_id"


@when(parsers.parse('I execute a command to domain "{domain}"'))
def _when_execute_to_domain(state: _State, domain: str) -> None:
    if domain == "nonexistent":
        state.command_failed = True
        state.error_type = "unknown_domain"
        state.error = "Unknown domain"
    else:
        state.command_succeeded = True


@when("I execute a command that produces 3 events")
def _when_execute_multi_event(state: _State) -> None:
    state.command_succeeded = True
    base = state.sequence
    state.events_returned.append(("Event1", base))
    state.events_returned.append(("Event2", base + 1))
    state.events_returned.append(("Event3", base + 2))


@when(parsers.parse('I query events for "{domain}" root "{root}"'))
def _when_query_events(state: _State, domain: str, root: str) -> None:
    count = state.aggregates.get(f"{domain}:{root}")
    if count is not None:
        for i in range(count):
            state.events_returned.append(("Event", i))


@when("I attempt to execute a command")
def _when_attempt_execute(state: _State) -> None:
    if not state.service_available:
        state.command_failed = True
        state.error_type = "connection"
        state.error = "Connection error"


@when(parsers.parse("I execute a command with timeout {timeout:d}ms"))
def _when_execute_with_timeout(state: _State, timeout: int) -> None:
    state.timeout_ms = timeout
    if state.service_slow:
        state.command_failed = True
        state.error_type = "timeout"
        state.error = "Deadline exceeded"


@when(
    parsers.parse(
        'I execute a "{cmd_type}" command for root "{root}" at sequence {seq:d}'
    )
)
def _when_execute_for_root(state: _State, cmd_type: str, root: str, seq: int) -> None:
    state.root = root
    if seq == 0:
        state.command_succeeded = True
        event_type = cmd_type.replace("Create", "Created")
        state.events_returned.append((event_type, 0))
        state.aggregates[f"{state.domain}:{root}"] = 1
    else:
        state.command_failed = True
        state.error_type = "precondition"


# --- Then -------------------------------------------------------------------


@then("the command should succeed")
def _then_command_succeeds(state: _State) -> None:
    assert state.command_succeeded, state.error


@then("the command should fail")
def _then_command_fails(state: _State) -> None:
    assert state.command_failed


@then(parsers.parse("the response should contain {count:d} event"))
def _then_response_contains_event(state: _State, count: int) -> None:
    assert len(state.events_returned) == count


@then(parsers.parse("the response should contain {count:d} events"))
def _then_response_contains_events(state: _State, count: int) -> None:
    assert len(state.events_returned) == count


@then(parsers.parse('the event should have type "{event_type}"'))
def _then_event_has_type(state: _State, event_type: str) -> None:
    assert state.events_returned
    assert state.events_returned[0][0] == event_type


@then(parsers.parse("the response should contain events starting at sequence {seq:d}"))
def _then_events_start_at(state: _State, seq: int) -> None:
    assert state.events_returned
    assert state.events_returned[0][1] == seq


@then(parsers.parse('the response events should have correlation ID "{cid}"'))
def _then_events_have_correlation(state: _State, cid: str) -> None:
    assert state.correlation_id == cid


@then("the command should fail with precondition error")
def _then_fail_precondition(state: _State) -> None:
    assert state.command_failed
    assert state.error_type == "precondition"


@then("the error should indicate sequence mismatch")
def _then_error_sequence_mismatch(state: _State) -> None:
    assert state.error is not None
    assert "Sequence" in state.error


@then("one should succeed")
def _then_one_succeeds(state: _State) -> None:
    assert any(state.concurrent_results)


@then("one should fail with precondition error")
def _then_one_fails_precondition(state: _State) -> None:
    assert any(not r for r in state.concurrent_results)


@then("the response should return without waiting for projectors")
def _then_async_returns(state: _State) -> None:
    assert state.sync_mode == "ASYNC"


@then("the response should include projector results")
def _then_includes_projector_results(state: _State) -> None:
    assert state.projectors_configured


@then("the response should include downstream saga results")
def _then_includes_saga_results(state: _State) -> None:
    assert state.sagas_configured


@then("the command should fail with invalid argument error")
def _then_fail_invalid_argument(state: _State) -> None:
    assert state.command_failed
    assert state.error_type == "invalid_argument"


@then("the error message should describe the missing field")
def _then_error_describes_field(state: _State) -> None:
    assert state.error is not None
    assert "field" in state.error


@then("the error should indicate unknown domain")
def _then_error_unknown_domain(state: _State) -> None:
    assert state.error_type == "unknown_domain"


@then(parsers.parse("events should have sequences {s1:d}, {s2:d}, {s3:d}"))
def _then_events_have_sequences(state: _State, s1: int, s2: int, s3: int) -> None:
    assert len(state.events_returned) == 3
    assert state.events_returned[0][1] == s1
    assert state.events_returned[1][1] == s2
    assert state.events_returned[2][1] == s3


@then("I should see all 3 events or none")
def _then_atomic_events(state: _State) -> None:
    assert len(state.events_returned) == 3 or not state.events_returned


@then("the aggregate operation should fail with connection error")
def _then_aggregate_fail_connection(state: _State) -> None:
    assert state.command_failed
    assert state.error_type == "connection"


@then("the operation should fail with timeout or deadline error")
def _then_fail_timeout(state: _State) -> None:
    assert state.command_failed
    assert state.error_type == "timeout"


@then(parsers.parse("the aggregate should now exist with {count:d} event"))
def _then_aggregate_exists(state: _State, count: int) -> None:
    key = f"{state.domain}:{state.root}"
    assert state.aggregates.get(key) == count
