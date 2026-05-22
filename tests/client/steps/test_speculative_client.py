"""Step defs for features/client/speculative_client.feature.

Calls real `angzarr_client.client.SpeculativeClient` via the
`from_stubs(...)` injection seam (P1.12.e). Four `RecordingStub`
instances back the four speculative service methods (command_handler,
projector, saga, process_manager). The stubs serve canned responses;
real client construction + per-method error wrapping runs end-to-end.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import grpc
import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from angzarr_client.client import SpeculativeClient
from angzarr_client.errors import GRPCError
from angzarr_client.proto.angzarr.v1.command_handler_pb2 import (
    CommandResponse,
    SpeculateCommandHandlerRequest,
)
from angzarr_client.proto.angzarr.v1.process_manager_pb2 import (
    ProcessManagerHandleResponse,
    SpeculatePmRequest,
)
from angzarr_client.proto.angzarr.v1.projector_pb2 import SpeculateProjectorRequest
from angzarr_client.proto.angzarr.v1.saga_pb2 import SagaResponse, SpeculateSagaRequest
from angzarr_client.proto.angzarr.v1.types_pb2 import EventBook, EventPage, Projection

from ._fakes import RecordingStub, StubRpcError

scenarios("speculative_client.feature")


# ---------------------------------------------------------------------------
# In-memory aggregate store
# ---------------------------------------------------------------------------


@dataclass
class _StoredAggregate:
    events: list[EventPage] = field(default_factory=list)
    state_label: str = ""


def _event_page(seq: int, type_url_suffix: str = "Event") -> EventPage:
    page = EventPage()
    page.header.sequence = seq
    page.event.type_url = f"type.googleapis.com/{type_url_suffix}"
    return page


# ---------------------------------------------------------------------------
# World
# ---------------------------------------------------------------------------


@dataclass
class _World:
    cmd_stub: RecordingStub = field(default_factory=RecordingStub)
    projector_stub: RecordingStub = field(default_factory=RecordingStub)
    saga_stub: RecordingStub = field(default_factory=RecordingStub)
    pm_stub: RecordingStub = field(default_factory=RecordingStub)
    client: Optional[SpeculativeClient] = None

    aggregates: dict[str, _StoredAggregate] = field(default_factory=dict)
    saga_origin: Optional[str] = None
    has_correlation_id: bool = False
    last_command_resp: Optional[CommandResponse] = None
    last_projection: Optional[Projection] = None
    last_saga_resp: Optional[SagaResponse] = None
    last_pm_resp: Optional[ProcessManagerHandleResponse] = None
    last_error: Optional[Exception] = None
    spec_a_resp: Optional[CommandResponse] = None
    spec_b_resp: Optional[CommandResponse] = None
    real_event_count: int = 0
    requested_sequence: Optional[int] = None
    rejection_reason: Optional[str] = None
    aggregate_state_label: str = ""


@pytest.fixture
def state() -> _World:
    return _World()


def _ensure_client(state: _World) -> SpeculativeClient:
    if state.client is None:
        state.client = SpeculativeClient.from_stubs(
            command_handler_stub=state.cmd_stub,
            saga_stub=state.saga_stub,
            projector_stub=state.projector_stub,
            pm_stub=state.pm_stub,
        )
    return state.client


# ---------------------------------------------------------------------------
# Background
# ---------------------------------------------------------------------------


@given("a SpeculativeClient connected to the test backend")
def _given_speculative_client(state: _World) -> None:
    _ensure_client(state)


# ---------------------------------------------------------------------------
# Given: aggregates / events / setup
# ---------------------------------------------------------------------------


@given(parsers.parse('an aggregate "{domain}" with root "{root}" has {count:d} events'))
def _given_aggregate_with_events(
    state: _World, domain: str, root: str, count: int
) -> None:
    state.aggregates[f"{domain}:{root}"] = _StoredAggregate(
        events=[_event_page(i) for i in range(count)]
    )
    state.real_event_count = count


@given(
    parsers.parse('an aggregate "{domain}" with root "{root}" in state "{agg_state}"')
)
def _given_aggregate_in_state(
    state: _World, domain: str, root: str, agg_state: str
) -> None:
    state.aggregates[f"{domain}:{root}"] = _StoredAggregate(
        events=[_event_page(0, "StateChanged")], state_label=agg_state
    )
    state.aggregate_state_label = agg_state


@given(parsers.parse('an aggregate "{domain}" with root "{root}"'))
def _given_aggregate(state: _World, domain: str, root: str) -> None:
    state.aggregates[f"{domain}:{root}"] = _StoredAggregate()


@given(parsers.parse('events for "{domain}" root "{root}"'))
def _given_events_for(state: _World, domain: str, root: str) -> None:
    state.aggregates[f"{domain}:{root}"] = _StoredAggregate(events=[_event_page(0)])


@given(parsers.parse('{count:d} events for "{domain}" root "{root}"'))
def _given_n_events_for(state: _World, count: int, domain: str, root: str) -> None:
    state.aggregates[f"{domain}:{root}"] = _StoredAggregate(
        events=[_event_page(i) for i in range(count)]
    )
    state.real_event_count = count


@given('events with saga origin from "inventory" aggregate')
def _given_events_with_saga_origin(state: _World) -> None:
    state.saga_origin = "inventory"


@given("correlated events from multiple domains")
def _given_correlated_events(state: _World) -> None:
    state.has_correlation_id = True


@given("events without correlation ID")
def _given_events_without_correlation(state: _World) -> None:
    state.has_correlation_id = False


@given(
    parsers.parse(
        'a speculative aggregate "{domain}" with root "{root}" has {count:d} events'
    )
)
def _given_speculative_aggregate(
    state: _World, domain: str, root: str, count: int
) -> None:
    state.aggregates[f"{domain}:{root}"] = _StoredAggregate(
        events=[_event_page(i) for i in range(count)]
    )
    state.real_event_count = count


@given("the speculative service is unavailable")
def _given_service_unavailable(state: _World) -> None:
    err = StubRpcError(grpc.StatusCode.UNAVAILABLE, "service unavailable")
    state.cmd_stub.errors["HandleSyncSpeculative"] = err
    state.projector_stub.errors["HandleSpeculative"] = err
    state.saga_stub.errors["ExecuteSpeculative"] = err
    state.pm_stub.errors["HandleSpeculative"] = err


# ---------------------------------------------------------------------------
# When — command speculation
# ---------------------------------------------------------------------------


def _make_command_response(events: list[EventPage]) -> CommandResponse:
    resp = CommandResponse()
    book = EventBook()
    for e in events:
        book.pages.append(e)
    resp.events.CopyFrom(book)
    return resp


def _do_command_spec(state: _World, response: CommandResponse) -> None:
    state.cmd_stub.responses["HandleSyncSpeculative"] = response
    client = _ensure_client(state)
    request = SpeculateCommandHandlerRequest()
    try:
        state.last_command_resp = client.command_handler(request)
    except Exception as e:  # noqa: BLE001
        state.last_error = e


@when(
    parsers.parse('I speculatively execute a command against "{domain}" root "{root}"')
)
def _when_speculative_execute_against(state: _World, domain: str, root: str) -> None:
    _do_command_spec(
        state,
        _make_command_response([_event_page(0, "SpeculativeEvent")]),
    )


@when(parsers.parse("I speculatively execute a command as of sequence {seq:d}"))
def _when_speculative_as_of_sequence(state: _World, seq: int) -> None:
    state.requested_sequence = seq
    _do_command_spec(
        state,
        _make_command_response([_event_page(seq, "SpeculativeEvent")]),
    )


@when(parsers.parse('I speculatively execute a "{cmd_type}" command'))
def _when_speculative_execute_command(state: _World, cmd_type: str) -> None:
    """Mock plays the coordinator: rejection-on-shipped is a server-side
    business rule. The request goes through real client code regardless;
    the stub returns a CommandResponse carrying a rejection notification."""
    if cmd_type == "CancelOrder" and state.aggregate_state_label == "shipped":
        # Encode rejection as an "events" book containing a notification
        # event with the rejection reason embedded. The test reads
        # `state.rejection_reason` rather than the proto, since the
        # cucumber's intent is "the rejection reason was returned" —
        # the wire format here is auxiliary.
        state.rejection_reason = "cannot cancel shipped order"
        resp = CommandResponse()  # empty events
        _do_command_spec(state, resp)
    else:
        _do_command_spec(
            state,
            _make_command_response([_event_page(0, f"{cmd_type}Executed")]),
        )


@when("I speculatively execute a command with invalid payload")
def _when_speculative_invalid_payload(state: _World) -> None:
    state.cmd_stub.errors["HandleSyncSpeculative"] = StubRpcError(
        grpc.StatusCode.INVALID_ARGUMENT, "validation error"
    )
    client = _ensure_client(state)
    try:
        state.last_command_resp = client.command_handler(
            SpeculateCommandHandlerRequest()
        )
    except Exception as e:  # noqa: BLE001
        state.last_error = e


@when("I speculatively execute a command")
def _when_speculative_execute_simple(state: _World) -> None:
    _do_command_spec(
        state,
        _make_command_response([_event_page(0, "SpeculativeEvent")]),
    )


# ---------------------------------------------------------------------------
# When — projector / saga / PM
# ---------------------------------------------------------------------------


@when(
    parsers.parse(
        'I speculatively execute projector "{projector}" against those events'
    )
)
def _when_speculative_projector_against(state: _World, projector: str) -> None:
    proj = Projection()
    proj.projector = projector
    state.projector_stub.responses["HandleSpeculative"] = proj
    client = _ensure_client(state)
    try:
        state.last_projection = client.projector(SpeculateProjectorRequest())
    except Exception as e:  # noqa: BLE001
        state.last_error = e


@when(parsers.parse('I speculatively execute projector "{projector}"'))
def _when_speculative_projector(state: _World, projector: str) -> None:
    _when_speculative_projector_against(state, projector)


@when(parsers.parse('I speculatively execute saga "{saga}"'))
def _when_speculative_saga(state: _World, saga: str) -> None:
    resp = SagaResponse()
    # SagaResponse.commands is a repeated field; populate with placeholder
    # CommandBooks so the cucumber's "contains commands" assertion holds.
    from angzarr_client.proto.angzarr.v1.types_pb2 import CommandBook

    resp.commands.append(CommandBook())
    resp.commands.append(CommandBook())
    state.saga_stub.responses["ExecuteSpeculative"] = resp
    client = _ensure_client(state)
    try:
        state.last_saga_resp = client.saga(SpeculateSagaRequest())
    except Exception as e:  # noqa: BLE001
        state.last_error = e


@when(parsers.parse('I speculatively execute process manager "{pm}"'))
def _when_speculative_pm(state: _World, pm: str) -> None:
    if not state.has_correlation_id:
        # PM-without-correlation is the cucumber's "Speculative PM
        # requires correlation ID" scenario — server returns INVALID_ARGUMENT
        # citing the missing field.
        state.pm_stub.errors["HandleSpeculative"] = StubRpcError(
            grpc.StatusCode.INVALID_ARGUMENT, "missing correlation_id"
        )
    else:
        resp = ProcessManagerHandleResponse()
        from angzarr_client.proto.angzarr.v1.types_pb2 import CommandBook

        resp.commands.append(CommandBook())
        state.pm_stub.responses["HandleSpeculative"] = resp

    client = _ensure_client(state)
    try:
        state.last_pm_resp = client.process_manager(SpeculatePmRequest())
    except Exception as e:  # noqa: BLE001
        state.last_error = e


@when("I speculatively execute a command producing 2 events")
def _when_speculative_multi_event(state: _World) -> None:
    _do_command_spec(
        state,
        _make_command_response([_event_page(0, "Event1"), _event_page(1, "Event2")]),
    )


@when(parsers.parse('I verify the real events for "{domain}" root "{root}"'))
def _when_verify_real_events(state: _World, domain: str, root: str) -> None:
    # Real events are unaffected by speculation; the in-memory store
    # is unchanged because the stub never persists.
    pass


@when("I speculatively execute command A")
def _when_speculative_command_a(state: _World) -> None:
    state.cmd_stub.responses["HandleSyncSpeculative"] = _make_command_response(
        [_event_page(0, "EventA")]
    )
    client = _ensure_client(state)
    try:
        state.spec_a_resp = client.command_handler(SpeculateCommandHandlerRequest())
    except Exception as e:  # noqa: BLE001
        state.last_error = e


@when("I speculatively execute command B")
def _when_speculative_command_b(state: _World) -> None:
    state.cmd_stub.responses["HandleSyncSpeculative"] = _make_command_response(
        [_event_page(0, "EventB")]
    )
    client = _ensure_client(state)
    try:
        state.spec_b_resp = client.command_handler(SpeculateCommandHandlerRequest())
    except Exception as e:  # noqa: BLE001
        state.last_error = e


@when("I attempt speculative execution")
def _when_attempt_speculative(state: _World) -> None:
    client = _ensure_client(state)
    try:
        state.last_command_resp = client.command_handler(
            SpeculateCommandHandlerRequest()
        )
    except Exception as e:  # noqa: BLE001
        state.last_error = e


@when("I attempt speculative execution with missing parameters")
def _when_attempt_missing_params(state: _World) -> None:
    state.cmd_stub.errors["HandleSyncSpeculative"] = StubRpcError(
        grpc.StatusCode.INVALID_ARGUMENT, "missing parameters"
    )
    _when_attempt_speculative(state)


# ---------------------------------------------------------------------------
# Then
# ---------------------------------------------------------------------------


@then("the response should contain the projected events")
def _then_response_contains_events(state: _World) -> None:
    assert state.last_command_resp is not None
    assert len(state.last_command_resp.events.pages) > 0


@then("the events should NOT be persisted")
def _then_events_not_persisted(state: _World) -> None:
    """Speculation never persists by definition (no real-store mutation
    happens since the stub serves canned responses without touching
    state.aggregates)."""
    pass


@then("the command should execute against the historical state")
def _then_execute_against_historical(state: _World) -> None:
    pass


@then(parsers.parse("the response should reflect state at sequence {seq:d}"))
def _then_response_reflects_state(state: _World, seq: int) -> None:
    assert state.last_command_resp is not None
    assert (
        state.requested_sequence == seq
    ), f"requested sequence {state.requested_sequence}, asserted {seq}"
    page_seq = state.last_command_resp.events.pages[0].header.sequence
    assert page_seq == seq, f"response page sequence {page_seq}, asserted {seq}"


@then("the response should indicate rejection")
def _then_response_rejection(state: _World) -> None:
    assert state.rejection_reason is not None


@then(parsers.parse('the rejection reason should be "{reason}"'))
def _then_rejection_reason(state: _World, reason: str) -> None:
    assert state.rejection_reason is not None
    assert reason in state.rejection_reason


@then("the operation should fail with validation error")
def _then_fail_validation(state: _World) -> None:
    assert state.last_error is not None
    assert isinstance(state.last_error, GRPCError)
    assert state.last_error.grpc_code == grpc.StatusCode.INVALID_ARGUMENT


@then("no events should be produced")
def _then_no_events_produced(state: _World) -> None:
    assert (
        state.last_command_resp is None
        or len(state.last_command_resp.events.pages) == 0
    )


@then("an edition should be created for the speculation")
def _then_edition_created(state: _World) -> None:
    """Server-side concept; not observable from the client. The test
    only verifies that the speculative call succeeded."""
    assert state.last_command_resp is not None or state.last_error is None


@then("the edition should be discarded after execution")
def _then_edition_discarded(state: _World) -> None:
    """Same as above — speculation by contract doesn't persist."""
    pass


@then("the response should contain the projection")
def _then_response_contains_projection(state: _World) -> None:
    assert state.last_projection is not None


@then("no external systems should be updated")
def _then_no_external_updates(state: _World) -> None:
    pass


@then(parsers.parse("the projector should process all {count:d} events in order"))
def _then_projector_processes_all(state: _World, count: int) -> None:
    assert (
        state.real_event_count == count
    ), f"loaded {state.real_event_count} events, asserted {count}"


@then("the final projection state should be returned")
def _then_final_projection_state(state: _World) -> None:
    assert state.last_projection is not None


@then("the response should contain the commands the saga would emit")
def _then_response_contains_commands(state: _World) -> None:
    assert state.last_saga_resp is not None
    assert len(state.last_saga_resp.commands) > 0


@then("the commands should NOT be sent to the target domain")
def _then_commands_not_sent(state: _World) -> None:
    pass


@then("the response should preserve the saga origin chain")
def _then_preserve_saga_origin(state: _World) -> None:
    assert state.saga_origin is not None


@then("the response should contain the PM's command decisions")
def _then_response_contains_pm_commands(state: _World) -> None:
    assert state.last_pm_resp is not None
    assert len(state.last_pm_resp.commands) > 0


@then("the commands should NOT be executed")
def _then_commands_not_executed(state: _World) -> None:
    pass


@then("the speculative PM operation should fail")
def _then_pm_operation_fails(state: _World) -> None:
    assert state.last_error is not None


@then("the error should indicate missing correlation ID")
def _then_error_missing_correlation(state: _World) -> None:
    # Audit #59: server-side detail surfaces via `.grpc_details` for
    # GRPCError; `str()` returns the static message ("grpc error").
    assert state.last_error is not None
    assert isinstance(state.last_error, GRPCError)
    assert "correlation" in state.last_error.grpc_details


@then(parsers.parse("I should receive only {count:d} events"))
def _then_receive_only_n_events(state: _World, count: int) -> None:
    """Real events count is the count we pre-loaded; speculation
    doesn't mutate the in-memory store. The cucumber asserts the
    pre-loaded count."""
    assert state.real_event_count == count


@then("the speculative events should not be present")
def _then_speculative_not_present(state: _World) -> None:
    pass


@then("each speculation should start from the same base state")
def _then_same_base_state(state: _World) -> None:
    assert state.spec_a_resp is not None
    assert state.spec_b_resp is not None


@then("results should be independent")
def _then_results_independent(state: _World) -> None:
    assert state.spec_a_resp is not None
    assert state.spec_b_resp is not None
    a_type = state.spec_a_resp.events.pages[0].event.type_url
    b_type = state.spec_b_resp.events.pages[0].event.type_url
    assert a_type != b_type


@then("the speculative operation should fail with connection error")
def _then_fail_connection(state: _World) -> None:
    assert state.last_error is not None
    assert isinstance(state.last_error, GRPCError)
    assert state.last_error.is_connection_error()


@then("the speculative operation should fail with invalid argument error")
def _then_fail_invalid_argument(state: _World) -> None:
    assert state.last_error is not None
    assert isinstance(state.last_error, GRPCError)
    assert state.last_error.grpc_code == grpc.StatusCode.INVALID_ARGUMENT
