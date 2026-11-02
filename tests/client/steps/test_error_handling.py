"""Step defs for parity/client/error_handling.feature.

Real-error-class port mirroring tests/steps/error_handling.rs. Uses
angzarr_client.errors directly so every predicate and gRPC status-code
round-trip is exercised against the production error hierarchy. A
builder's and a timestamp's error come from the builders themselves, and the
access denial from a real call to a server answering one (decision 14.37:
every error is asserted by its code and args, step rule 18).
"""

from __future__ import annotations

from collections.abc import Iterator
from concurrent import futures
from dataclasses import dataclass, field
from uuid import uuid4

import grpc
import pytest
from google.protobuf import any_pb2, duration_pb2
from google.rpc import error_details_pb2
from pytest_bdd import given, parsers, scenarios, then, when

from angzarr_client import CommandBuilder, QueryBuilder, QueryClient
from angzarr_client._pb import Query
from angzarr_client.diagnostic import display, render
from angzarr_client.error_codes import codes, keys
from angzarr_client.errors import (
    ClientError,
    ConnectionError,
    GRPCError,
    InvalidArgumentError,
    InvalidTimestampError,
    TransportError,
)
from angzarr_client.proto.io.angzarr.transport.distributed.v1 import query_pb2_grpc
from angzarr_client.proto.io.angzarr.v1 import reason_args
from angzarr_client.router import CodedError, GrpcCode

scenarios("parity/client/error_handling.feature")


class _StubRpcError:
    """Minimal stand-in for grpc.RpcError — implements code() and details(),
    and trailing_metadata() when given the status details trailer."""

    def __init__(self, code: grpc.StatusCode, details: str, trailer: bytes = b""):
        self._code = code
        self._details = details
        self._trailer = trailer

    def code(self) -> grpc.StatusCode:
        return self._code

    def details(self) -> str:
        return self._details

    def trailing_metadata(self):
        return (("grpc-status-details-bin", self._trailer),) if self._trailer else ()


def _grpc_error(code: grpc.StatusCode, details: str) -> GRPCError:
    return GRPCError(_StubRpcError(code, details))


# The access denial the restricted operation's server answers: a business
# reason key and its args, as the component host sends a coded failure.
_DENIAL = CodedError(
    "ACCESS_DENIED", GrpcCode.PERMISSION_DENIED, {"permission": "orders.write"}
)

# The detailed status: a framework reason key with its args, and a second
# detail after the ErrorInfo.
_DETAILED = CodedError(
    codes.ROUTER_REASON_UNHANDLED_HANDLER_ERROR,
    GrpcCode.INTERNAL,
    {reason_args.CAUSE: codes.ROUTER_REASON_HANDLER_PANICKED},
)
_RETRY = error_details_pb2.RetryInfo(retry_delay=duration_pb2.Duration(seconds=2))


class _DenyingQuery(query_pb2_grpc.EventQueryServiceServicer):
    """An event query service refusing every read with :data:`_DENIAL`."""

    def GetEventBook(self, request, context):
        status = _DENIAL.status()
        context.set_trailing_metadata(
            (("grpc-status-details-bin", status.SerializeToString()),)
        )
        context.abort(grpc.StatusCode.PERMISSION_DENIED, status.message)


@dataclass
class _State:
    current_error: ClientError | None = None
    error_variants: list[ClientError] = field(default_factory=list)
    server: grpc.Server | None = None
    endpoint: str = ""


@pytest.fixture
def state() -> Iterator[_State]:
    world = _State()
    yield world
    if world.server is not None:
        world.server.stop(grace=None).wait()


def _capture(operation) -> ClientError:
    try:
        operation()
    except ClientError as e:
        return e
    raise AssertionError("the operation succeeded")


# --- Given ------------------------------------------------------------------


@given("the server is unreachable")
def _given_server_unreachable(state: _State) -> None:
    state.current_error = ConnectionError()


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
    state.current_error = _grpc_error(
        grpc.StatusCode.FAILED_PRECONDITION,
        f"sequence mismatch: expected {seq}, got 3",
    )


@given("the client lacks required permissions")
def _given_lacks_permissions(state: _State) -> None:
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    query_pb2_grpc.add_EventQueryServiceServicer_to_server(_DenyingQuery(), server)
    port = server.add_insecure_port("localhost:0")
    server.start()
    state.server = server
    state.endpoint = f"localhost:{port}"


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
    state.current_error = InvalidArgumentError(
        codes.PROTO_UUID_INVALID, details={keys.FIELD: "root"}
    )


@given("a gRPC error with status NOT_FOUND")
def _given_grpc_not_found(state: _State) -> None:
    state.current_error = _grpc_error(grpc.StatusCode.NOT_FOUND, "resource not found")


@given("a connection error")
def _given_connection_error(state: _State) -> None:
    state.current_error = ConnectionError()


@given("a gRPC error with detailed status")
def _given_grpc_detailed_status(state: _State) -> None:
    status = _DETAILED.status()
    retry = any_pb2.Any()
    retry.Pack(_RETRY)
    status.details.append(retry)
    state.current_error = GRPCError(
        _StubRpcError(
            grpc.StatusCode.INTERNAL, status.message, status.SerializeToString()
        )
    )


@given("an invalid argument error")
def _given_invalid_argument_error(state: _State) -> None:
    state.current_error = InvalidArgumentError(codes.COMMAND_TYPE_URL_MISSING)


@given("different error types")
def _given_different_error_types(state: _State) -> None:
    state.error_variants = [
        _grpc_error(grpc.StatusCode.NOT_FOUND, "not found"),
        _grpc_error(grpc.StatusCode.FAILED_PRECONDITION, "precondition failed"),
        _grpc_error(grpc.StatusCode.INVALID_ARGUMENT, "invalid"),
        _grpc_error(grpc.StatusCode.INTERNAL, "internal"),
        ConnectionError(),
        InvalidArgumentError(codes.COMMAND_PAYLOAD_MISSING),
        TransportError(RuntimeError("transport")),
    ]


@given("various error types")
def _given_various_error_types(state: _State) -> None:
    state.error_variants = [
        ConnectionError(),
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
    pass


@when("I build a command without required fields")
def _when_build_without_required(state: _State) -> None:
    builder = CommandBuilder(None, "orders", uuid4())  # type: ignore[arg-type]
    state.current_error = _capture(builder.build)


@when("I build a query with invalid timestamp format")
def _when_build_invalid_timestamp(state: _State) -> None:
    builder = QueryBuilder(None, "orders", uuid4())  # type: ignore[arg-type]
    state.current_error = _capture(lambda: builder.as_of_time("yesterday at noon"))


@when("I send a malformed request to the server")
def _when_send_malformed(state: _State) -> None:
    state.current_error = _grpc_error(
        grpc.StatusCode.INVALID_ARGUMENT, "malformed request"
    )


@when("I attempt a restricted operation")
def _when_attempt_restricted(state: _State) -> None:
    client = QueryClient.connect(state.endpoint)
    try:
        state.current_error = _capture(lambda: client.get_event_book(Query()))
    finally:
        client.close()


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


@then("the error should be an invalid timestamp error")
def _then_is_invalid_timestamp(state: _State) -> None:
    assert isinstance(_err(state), InvalidTimestampError)


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
    assert _err(state).is_precondition_failed()


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
    assert err.status().code == grpc.StatusCode.INTERNAL.value[0]
    assert len(err.status().details) == 2


@then("I can access the status code, its message (the reason key), and details")
def _then_access_status_parts(state: _State) -> None:
    status = _err(state).status()
    assert status.code == GrpcCode.INTERNAL
    assert status.message == codes.ROUTER_REASON_UNHANDLED_HANDLER_ERROR
    info = error_details_pb2.ErrorInfo()
    assert status.details[0].Unpack(info)
    assert info.reason == status.message
    assert dict(info.metadata) == {
        reason_args.CAUSE: codes.ROUTER_REASON_HANDLER_PANICKED
    }
    retry = error_details_pb2.RetryInfo()
    assert status.details[1].Unpack(retry)
    assert retry == _RETRY


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


@then("I should get detailed diagnostic information")
def _then_get_diagnostic_info(state: _State) -> None:
    assert repr(_err(state))


# --- decision 14.37: a code and its args, English from the catalog ------------


@then("I should get the catalog's text for the error's code, filled with its args")
def _then_catalog_text(state: _State) -> None:
    assert state.current_error.message == "root is not a UUID"


@then("no text in it came from the server")
def _then_no_server_text(state: _State) -> None:
    # The error was never given text: its English is all the catalog's.
    assert state.current_error.cause is None
    assert state.current_error.message != str(state.current_error)


@then("I should get the error's code followed by its args")
def _then_code_and_args(state: _State) -> None:
    assert str(state.current_error) == "PROTO_UUID_INVALID field=root"


@then("it carries no rendered text")
def _then_no_rendered_text(state: _State) -> None:
    assert state.current_error.message not in str(state.current_error)


def _assert_coded(err: ClientError, code: str, args: dict[str, str]) -> None:
    """``err`` is ``code`` with exactly ``args``: its text the code then the
    args, its English the catalog's for the code."""
    assert err.code == code, err.code
    assert err.details == args, err.details
    assert str(err) == display(code, args)
    assert err.message == render(code, args)


@then("the error's code and args should name the connection failure")
def _then_names_connection_failure(state: _State) -> None:
    _assert_coded(_err(state), codes.CONNECTION_FAILED, {})
    assert _err(state).message == "connection failed"


@then("the error's code and args should name what's missing")
def _then_names_missing(state: _State) -> None:
    _assert_coded(_err(state), codes.COMMAND_TYPE_URL_MISSING, {})
    assert _err(state).message == "command type_url not set"


@then("the error's code and args should name the format problem")
def _then_names_format_problem(state: _State) -> None:
    # The arg is protobuf's own text, a third party's, carried verbatim.
    err = _err(state)
    assert set(err.details) == {"ext_err_txt"}, err.details
    assert err.details["ext_err_txt"]
    _assert_coded(err, codes.TIMESTAMP_PARSE_FAILED, err.details)
    assert isinstance(err.__cause__, ValueError)


@then("the error's code and args should name the access denial")
def _then_names_access_denial(state: _State) -> None:
    err = _err(state)
    _assert_coded(err, _DENIAL.code, _DENIAL.extras)
    assert err.status().message == _DENIAL.code
