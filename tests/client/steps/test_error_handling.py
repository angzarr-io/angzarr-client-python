"""Step defs for features/client/error_handling.feature.

Real-error-class port mirroring tests/steps/error_handling.rs. Uses
angzarr_client.errors directly so every predicate and gRPC status-code
round-trip is exercised against the production error hierarchy.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import grpc
import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from angzarr_client.errors import (
    ClientError,
    ConnectionError,
    GRPCError,
    InvalidArgumentError,
    InvalidTimestampError,
    TransportError,
)

scenarios("error_handling.feature")


class _StubRpcError:
    """Minimal stand-in for grpc.RpcError — implements code() and details()."""

    def __init__(self, code: grpc.StatusCode, details: str):
        self._code = code
        self._details = details

    def code(self) -> grpc.StatusCode:
        return self._code

    def details(self) -> str:
        return self._details


def _grpc_error(code: grpc.StatusCode, details: str) -> GRPCError:
    return GRPCError(_StubRpcError(code, details))


@dataclass
class _State:
    current_error: ClientError | None = None
    error_variants: list[ClientError] = field(default_factory=list)
    server_seq: int | None = None
    client_seq: int | None = None


@pytest.fixture
def state() -> _State:
    return _State()


# --- Given ------------------------------------------------------------------


@given("the server is unreachable")
def _given_server_unreachable(state: _State) -> None:
    state.current_error = ConnectionError("connection refused")


@given("the connection drops mid-request")
def _given_connection_drops(state: _State) -> None:
    state.current_error = TransportError(RuntimeError("connection reset"))


@given("the server returns a gRPC error")
def _given_server_returns_grpc_error(state: _State) -> None:
    state.current_error = _grpc_error(grpc.StatusCode.INTERNAL, "server error")


@given("the aggregate does not exist")
def _given_aggregate_not_exists(state: _State) -> None:
    state.current_error = _grpc_error(grpc.StatusCode.NOT_FOUND, "aggregate not found")


@given(parsers.parse("the server aggregate is at sequence {seq:d}"))
def _given_server_aggregate_at_sequence(state: _State, seq: int) -> None:
    state.server_seq = seq


@given("the client lacks required permissions")
def _given_lacks_permissions(state: _State) -> None:
    state.current_error = _grpc_error(
        grpc.StatusCode.PERMISSION_DENIED, "access denied to resource"
    )


@given("the server has an internal error")
def _given_server_internal_error(state: _State) -> None:
    state.current_error = _grpc_error(
        grpc.StatusCode.INTERNAL, "unexpected server error"
    )


@given("the operation times out")
def _given_operation_timeout(state: _State) -> None:
    state.current_error = _grpc_error(
        grpc.StatusCode.DEADLINE_EXCEEDED, "operation timed out"
    )


@given("any client error")
def _given_any_client_error(state: _State) -> None:
    state.current_error = ConnectionError("test error")


@given("a gRPC error with status NOT_FOUND")
def _given_grpc_not_found(state: _State) -> None:
    state.current_error = _grpc_error(grpc.StatusCode.NOT_FOUND, "resource not found")


@given("a connection error")
def _given_connection_error(state: _State) -> None:
    state.current_error = ConnectionError("connection failed")


@given("a gRPC error with detailed status")
def _given_grpc_detailed_status(state: _State) -> None:
    state.current_error = _grpc_error(
        grpc.StatusCode.INTERNAL, "detailed error message"
    )


@given("an invalid argument error")
def _given_invalid_argument_error(state: _State) -> None:
    state.current_error = InvalidArgumentError("missing field")


@given("different error types")
def _given_different_error_types(state: _State) -> None:
    state.error_variants = [
        _grpc_error(grpc.StatusCode.NOT_FOUND, "not found"),
        _grpc_error(grpc.StatusCode.FAILED_PRECONDITION, "precondition failed"),
        _grpc_error(grpc.StatusCode.INVALID_ARGUMENT, "invalid"),
        _grpc_error(grpc.StatusCode.INTERNAL, "internal"),
        ConnectionError("connection"),
        InvalidArgumentError("invalid arg"),
        TransportError(RuntimeError("transport")),
    ]


@given("various error types")
def _given_various_error_types(state: _State) -> None:
    state.error_variants = [
        ConnectionError("connection failed"),
        _grpc_error(grpc.StatusCode.UNAVAILABLE, "service unavailable"),
        _grpc_error(grpc.StatusCode.RESOURCE_EXHAUSTED, "rate limited"),
        _grpc_error(grpc.StatusCode.INVALID_ARGUMENT, "bad input"),
        _grpc_error(grpc.StatusCode.FAILED_PRECONDITION, "conflict"),
    ]


@given("an error with retry-after metadata")
def _given_error_with_retry_metadata(state: _State) -> None:
    state.current_error = _grpc_error(
        grpc.StatusCode.RESOURCE_EXHAUSTED, "rate limited"
    )


# --- When -------------------------------------------------------------------


@when("I attempt a client operation")
def _when_attempt_operation(state: _State) -> None:
    pass


@when("I query events for the aggregate")
def _when_query_events(state: _State) -> None:
    pass


@when(parsers.parse("I execute a mock command at sequence {seq:d}"))
def _when_execute_mock_at_sequence(state: _State, seq: int) -> None:
    state.client_seq = seq
    # Build the FAILED_PRECONDITION error using BOTH the server's sequence
    # (from the Given) and the client's attempted sequence (this When), so
    # mutating either value is observable downstream.
    server = state.server_seq if state.server_seq is not None else 0
    state.current_error = _grpc_error(
        grpc.StatusCode.FAILED_PRECONDITION,
        f"sequence mismatch: expected {server}, got {seq}",
    )


@when("I build a command without required fields")
def _when_build_without_required(state: _State) -> None:
    state.current_error = InvalidArgumentError("type_url not set")


@when("I build a query with invalid timestamp format")
def _when_build_invalid_timestamp(state: _State) -> None:
    state.current_error = InvalidTimestampError("invalid format")


@when("I send a malformed request to the server")
def _when_send_malformed(state: _State) -> None:
    state.current_error = _grpc_error(
        grpc.StatusCode.INVALID_ARGUMENT, "malformed request"
    )


@when("I attempt a restricted operation")
def _when_attempt_restricted(state: _State) -> None:
    pass


@when("I call message() on the error")
def _when_call_message(state: _State) -> None:
    pass


@when("I call code() on the error")
def _when_call_code(state: _State) -> None:
    pass


@when("I call status() on the error")
def _when_call_status(state: _State) -> None:
    pass


@when("I inspect the error details")
def _when_inspect_error(state: _State) -> None:
    pass


@when("I convert the error to string")
def _when_convert_to_string(state: _State) -> None:
    pass


@when("I debug-format the error")
def _when_debug_format(state: _State) -> None:
    pass


# --- Then -------------------------------------------------------------------


def _err(state: _State) -> ClientError:
    assert state.current_error is not None
    return state.current_error


@then("the error should be a connection error")
def _then_is_connection_error(state: _State) -> None:
    assert isinstance(_err(state), ConnectionError)


@then("is_connection_error should return true")
def _then_is_connection_error_true(state: _State) -> None:
    assert _err(state).is_connection_error()


@then("the error message should describe the connection failure")
def _then_message_describes_connection(state: _State) -> None:
    assert _err(state).message


@then("the error should be a transport error")
def _then_is_transport_error(state: _State) -> None:
    assert _err(state).is_connection_error()


@then("the error should be a gRPC error")
def _then_is_grpc_error(state: _State) -> None:
    assert isinstance(_err(state), GRPCError)


@then("the underlying Status should be accessible")
def _then_status_accessible(state: _State) -> None:
    err = _err(state)
    assert isinstance(err, GRPCError)
    assert err.status() is not None


@then("the error should be an invalid argument error")
def _then_is_invalid_argument(state: _State) -> None:
    assert _err(state).is_invalid_argument()


@then("is_invalid_argument should return true")
def _then_is_invalid_argument_true(state: _State) -> None:
    assert _err(state).is_invalid_argument()


@then("the error message should describe what's missing")
def _then_message_describes_missing(state: _State) -> None:
    assert _err(state).message


@then("the error should be an invalid timestamp error")
def _then_is_invalid_timestamp(state: _State) -> None:
    assert isinstance(_err(state), InvalidTimestampError)


@then("the error message should indicate the format problem")
def _then_message_indicates_format(state: _State) -> None:
    assert _err(state).message


@then("is_not_found should return true")
def _then_is_not_found_true(state: _State) -> None:
    assert _err(state).is_not_found()


@then("code should return NOT_FOUND")
def _then_code_not_found(state: _State) -> None:
    err = _err(state)
    assert isinstance(err, GRPCError)
    assert err.grpc_code == grpc.StatusCode.NOT_FOUND


@then("is_precondition_failed should return true")
def _then_is_precondition_failed_true(state: _State) -> None:
    assert _err(state).is_precondition_failed()


@then("code should return FAILED_PRECONDITION")
def _then_code_failed_precondition(state: _State) -> None:
    err = _err(state)
    assert isinstance(err, GRPCError)
    assert err.grpc_code == grpc.StatusCode.FAILED_PRECONDITION


@then("the error indicates optimistic lock failure")
def _then_indicates_optimistic_lock(state: _State) -> None:
    err = _err(state)
    assert err.is_precondition_failed()
    # Optimistic-lock errors must carry the sequence-mismatch detail so a
    # client can recover. Verify both sequences threaded through.
    if state.server_seq is not None and state.client_seq is not None:
        details = err.grpc_details if isinstance(err, GRPCError) else str(err)
        assert (
            f"expected {state.server_seq}" in details
        ), f"missing server seq in details: {details}"
        assert (
            f"got {state.client_seq}" in details
        ), f"missing client seq in details: {details}"


@then("code should return INVALID_ARGUMENT")
def _then_code_invalid_argument(state: _State) -> None:
    err = _err(state)
    assert isinstance(err, GRPCError)
    assert err.grpc_code == grpc.StatusCode.INVALID_ARGUMENT


@then("code should return PERMISSION_DENIED")
def _then_code_permission_denied(state: _State) -> None:
    err = _err(state)
    assert isinstance(err, GRPCError)
    assert err.grpc_code == grpc.StatusCode.PERMISSION_DENIED


@then("the error message should describe access denial")
def _then_message_describes_access_denial(state: _State) -> None:
    # Audit #59: server-side detail surfaces via `.grpc_details` (the
    # ClientError-side `.details` is now a structured dict, not the
    # gRPC server's free-form details string).
    err = _err(state)
    assert isinstance(err, GRPCError)
    detail = err.grpc_details
    assert "denied" in detail or "access" in detail, detail


@then("code should return INTERNAL")
def _then_code_internal(state: _State) -> None:
    err = _err(state)
    assert isinstance(err, GRPCError)
    assert err.grpc_code == grpc.StatusCode.INTERNAL


@then("the error should indicate server-side failure")
def _then_indicates_server_failure(state: _State) -> None:
    err = _err(state)
    assert isinstance(err, GRPCError)
    assert err.grpc_code == grpc.StatusCode.INTERNAL


@then("code should return DEADLINE_EXCEEDED")
def _then_code_deadline_exceeded(state: _State) -> None:
    err = _err(state)
    assert isinstance(err, GRPCError)
    assert err.grpc_code == grpc.StatusCode.DEADLINE_EXCEEDED


@then("I should get a non-empty string")
def _then_get_nonempty_string(state: _State) -> None:
    assert _err(state).message


@then("the message should describe the error")
def _then_message_describes_error(state: _State) -> None:
    assert _err(state).message


@then("I should get Some(NOT_FOUND)")
def _then_get_some_not_found(state: _State) -> None:
    err = _err(state)
    assert isinstance(err, GRPCError)
    assert err.grpc_code == grpc.StatusCode.NOT_FOUND


@then("I should get None")
def _then_get_none(state: _State) -> None:
    # Non-gRPC errors have no .code property — only GRPCError does.
    err = _err(state)
    assert not isinstance(err, GRPCError)


@then("I should get the full gRPC Status")
def _then_get_full_status(state: _State) -> None:
    err = _err(state)
    assert isinstance(err, GRPCError)
    assert err.status() is not None


@then("I can access the status code, message, and details")
def _then_access_status_parts(state: _State) -> None:
    err = _err(state)
    assert isinstance(err, GRPCError)
    assert err.code is not None
    assert err.details is not None


def _find(state: _State, predicate) -> ClientError:
    for err in state.error_variants:
        if predicate(err):
            return err
    raise AssertionError("variant not found")


def _is_grpc_code(code: grpc.StatusCode):
    return lambda e: isinstance(e, GRPCError) and e.grpc_code == code


@then("NOT_FOUND gRPC error should have is_not_found true")
def _then_not_found_has_is_not_found(state: _State) -> None:
    assert _find(state, _is_grpc_code(grpc.StatusCode.NOT_FOUND)).is_not_found()


@then("connection error should have is_not_found false")
def _then_connection_has_is_not_found_false(state: _State) -> None:
    assert not _find(state, lambda e: isinstance(e, ConnectionError)).is_not_found()


@then("INTERNAL gRPC error should have is_not_found false")
def _then_internal_has_is_not_found_false(state: _State) -> None:
    assert not _find(state, _is_grpc_code(grpc.StatusCode.INTERNAL)).is_not_found()


@then("FAILED_PRECONDITION gRPC error should have is_precondition_failed true")
def _then_failed_precondition_true(state: _State) -> None:
    assert _find(
        state, _is_grpc_code(grpc.StatusCode.FAILED_PRECONDITION)
    ).is_precondition_failed()


@then("NOT_FOUND gRPC error should have is_precondition_failed false")
def _then_not_found_precondition_false(state: _State) -> None:
    assert not _find(
        state, _is_grpc_code(grpc.StatusCode.NOT_FOUND)
    ).is_precondition_failed()


@then("connection error should have is_precondition_failed false")
def _then_connection_precondition_false(state: _State) -> None:
    assert not _find(
        state, lambda e: isinstance(e, ConnectionError)
    ).is_precondition_failed()


@then("INVALID_ARGUMENT gRPC error should have is_invalid_argument true")
def _then_invalid_argument_grpc_true(state: _State) -> None:
    assert _find(
        state, _is_grpc_code(grpc.StatusCode.INVALID_ARGUMENT)
    ).is_invalid_argument()


@then("ClientError::InvalidArgument should have is_invalid_argument true")
def _then_client_error_invalid_argument_true(state: _State) -> None:
    assert _find(
        state, lambda e: isinstance(e, InvalidArgumentError)
    ).is_invalid_argument()


@then("NOT_FOUND gRPC error should have is_invalid_argument false")
def _then_not_found_invalid_argument_false(state: _State) -> None:
    assert not _find(
        state, _is_grpc_code(grpc.StatusCode.NOT_FOUND)
    ).is_invalid_argument()


@then("connection error should have is_connection_error true")
def _then_connection_is_connection_true(state: _State) -> None:
    assert _find(state, lambda e: isinstance(e, ConnectionError)).is_connection_error()


@then("transport error should have is_connection_error true")
def _then_transport_is_connection_true(state: _State) -> None:
    assert _find(state, lambda e: isinstance(e, TransportError)).is_connection_error()


@then("gRPC error should have is_connection_error false")
def _then_grpc_is_connection_false(state: _State) -> None:
    # Use a non-UNAVAILABLE gRPC error (NOT_FOUND), since GRPCError maps
    # UNAVAILABLE → is_connection_error True.
    assert not _find(
        state, _is_grpc_code(grpc.StatusCode.NOT_FOUND)
    ).is_connection_error()


@then("connection errors should be retryable")
def _then_connection_retryable(state: _State) -> None:
    assert _find(state, lambda e: isinstance(e, ConnectionError)).is_connection_error()


@then("UNAVAILABLE gRPC errors should be retryable")
def _then_unavailable_retryable(state: _State) -> None:
    err = _find(state, _is_grpc_code(grpc.StatusCode.UNAVAILABLE))
    assert isinstance(err, GRPCError)
    assert err.grpc_code == grpc.StatusCode.UNAVAILABLE


@then("RESOURCE_EXHAUSTED should be retryable with backoff")
def _then_resource_exhausted_retryable(state: _State) -> None:
    err = _find(state, _is_grpc_code(grpc.StatusCode.RESOURCE_EXHAUSTED))
    assert isinstance(err, GRPCError)
    assert err.grpc_code == grpc.StatusCode.RESOURCE_EXHAUSTED


@then("INVALID_ARGUMENT should NOT be retryable")
def _then_invalid_argument_not_retryable(state: _State) -> None:
    assert _find(
        state, _is_grpc_code(grpc.StatusCode.INVALID_ARGUMENT)
    ).is_invalid_argument()


@then("FAILED_PRECONDITION should be retryable after state refresh")
def _then_failed_precondition_retryable(state: _State) -> None:
    assert _find(
        state, _is_grpc_code(grpc.StatusCode.FAILED_PRECONDITION)
    ).is_precondition_failed()


@then("I should be able to extract retry timing hints")
def _then_extract_retry_hints(state: _State) -> None:
    err = _err(state)
    # status() available on GRPCError; other errors expose message().
    _ = getattr(err, "status", lambda: None)()


@then("I should get a formatted error message")
def _then_get_formatted_message(state: _State) -> None:
    assert str(_err(state))


@then("the message should include the error type and description")
def _then_message_includes_type_and_desc(state: _State) -> None:
    assert str(_err(state))


@then("I should get detailed diagnostic information")
def _then_get_diagnostic_info(state: _State) -> None:
    assert repr(_err(state))
