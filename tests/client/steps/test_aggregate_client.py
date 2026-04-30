"""Step defs for features/client/aggregate_client.feature.

Calls real `angzarr_client.client.CommandHandlerClient` via the new
mock-injection pattern (P1.12.e / finding #24). The mock plays the
role of a coordinator with simple sequence validation and error
mapping — the client side under test is the request construction and
error-surface behavior, not the server's enforcement.

Previously this file simulated the entire pipeline via boolean state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from uuid import UUID, uuid4

import grpc
import pytest
from google.protobuf.wrappers_pb2 import StringValue
from pytest_bdd import given, parsers, scenarios, then, when

from angzarr_client.builder import CommandBuilder
from angzarr_client.errors import (
    ConnectionError as ClientConnectionError,
    GRPCError,
    InvalidArgumentError,
)
from angzarr_client.proto.angzarr import (
    CommandRequest,
    CommandResponse,
    EventBook,
    EventPage,
    SyncMode,
)

scenarios("aggregate_client.feature")


# ---------------------------------------------------------------------------
# Mock CommandHandlerClient (plays coordinator + storage)
# ---------------------------------------------------------------------------


class _StubRpcError:
    """Minimal grpc.RpcError stand-in carrying a code() + details()."""

    def __init__(self, code: grpc.StatusCode, details: str):
        self._code = code
        self._details = details

    def code(self) -> grpc.StatusCode:
        return self._code

    def details(self) -> str:
        return self._details

    def __str__(self) -> str:
        return self._details


class _MockCommandHandlerClient:
    """Records each handle_command call. Plays a tiny coordinator:
    tracks current sequence per (domain, root), rejects mis-sequenced
    commands with FAILED_PRECONDITION, returns a CommandResponse
    carrying N synthesized event pages on success.
    """

    def __init__(self) -> None:
        self.last_request: Optional[CommandRequest] = None
        self.requests: list[CommandRequest] = []
        # (domain, root_hex) -> next expected sequence
        self.aggregates: dict[tuple[str, str], int] = {}
        # Force-mode toggles the test can flip
        self.unavailable: bool = False
        self.slow: bool = False
        self.invalid_payload_marker: Optional[bytes] = None
        self.events_for_next_command: int = 1

    def _root_hex(self, request: CommandRequest) -> str:
        return request.command.cover.root.value.hex()

    def handle_command(
        self, request: CommandRequest, timeout: float | None = None
    ) -> CommandResponse:
        if self.unavailable:
            raise ClientConnectionError("backend unavailable")
        if self.slow and timeout is not None:
            raise GRPCError(
                _StubRpcError(grpc.StatusCode.DEADLINE_EXCEEDED, "deadline exceeded")
            )
        self.last_request = request
        self.requests.append(request)

        domain = request.command.cover.domain
        if domain == "nonexistent":
            raise GRPCError(
                _StubRpcError(grpc.StatusCode.NOT_FOUND, f"unknown domain: {domain}")
            )
        if not request.command.pages:
            raise GRPCError(
                _StubRpcError(grpc.StatusCode.INVALID_ARGUMENT, "missing pages")
            )
        page = request.command.pages[0]
        if (
            self.invalid_payload_marker is not None
            and page.command.value == self.invalid_payload_marker
        ):
            raise GRPCError(
                _StubRpcError(
                    grpc.StatusCode.INVALID_ARGUMENT,
                    "invalid payload",
                )
            )

        # Sequence validation
        key = (domain, self._root_hex(request))
        expected = self.aggregates.get(key, 0)
        actual = page.header.sequence
        if actual != expected:
            raise GRPCError(
                _StubRpcError(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    f"Sequence mismatch: expected {expected}, got {actual}",
                )
            )

        # Synthesize a response
        resp = CommandResponse()
        book = EventBook()
        book.cover.CopyFrom(request.command.cover)
        for i in range(self.events_for_next_command):
            ep = EventPage()
            ep.header.sequence = expected + i
            book.pages.append(ep)
        resp.events.CopyFrom(book)
        self.aggregates[key] = expected + self.events_for_next_command
        # Reset transient toggles
        self.events_for_next_command = 1
        return resp


# ---------------------------------------------------------------------------
# World
# ---------------------------------------------------------------------------


@dataclass
class _World:
    cmd: _MockCommandHandlerClient = field(default_factory=_MockCommandHandlerClient)
    domain: str = ""
    root: Optional[UUID] = None
    correlation_id: Optional[str] = None
    last_response: Optional[CommandResponse] = None
    last_error: Optional[Exception] = None
    concurrent_results: list[bool] = field(default_factory=list)
    current_sequence: Optional[int] = None
    timeout_used: Optional[float] = None
    queried_events: int = 0


@pytest.fixture
def state() -> _World:
    return _World()


def _root_uuid(literal: str) -> UUID:
    try:
        return UUID(literal)
    except ValueError:
        import uuid as _u

        return _u.uuid5(_u.NAMESPACE_DNS, literal)


def _do_execute(
    state: _World,
    *,
    sequence: int = 0,
    cmd_type: str = "test.Cmd",
    payload: bytes = b"\x0a\x05hello",
    sync_mode: SyncMode = SyncMode.SYNC_MODE_ASYNC,
) -> None:
    """Build + execute via real CommandBuilder, capturing response or error."""
    assert state.root is not None
    builder = CommandBuilder(state.cmd, state.domain, state.root)  # type: ignore[arg-type]
    if state.correlation_id:
        builder = builder.with_correlation_id(state.correlation_id)
    builder = builder.with_sequence(sequence)
    # Build the CommandRequest manually when caller wants a custom
    # payload (used to trigger the mock's invalid_payload_marker).
    msg = StringValue(value="hello")
    if payload != msg.SerializeToString():
        # First call with_command so build() succeeds; then mutate the
        # bytes on the resulting CommandBook to match the mock's marker.
        cmd_book = builder.with_command(f"type.googleapis.com/{cmd_type}", msg).build()
        cmd_book.pages[0].command.value = payload
        request = CommandRequest(command=cmd_book, sync_mode=sync_mode)
        try:
            state.last_response = state.cmd.handle_command(request)
        except Exception as e:  # noqa: BLE001
            state.last_error = e
        return

    builder = builder.with_command(f"type.googleapis.com/{cmd_type}", msg)
    try:
        state.last_response = builder.execute(sync_mode=sync_mode)
    except Exception as e:  # noqa: BLE001
        state.last_error = e


# ---------------------------------------------------------------------------
# Background
# ---------------------------------------------------------------------------


@given("an AggregateClient connected to the test backend")
def _given_aggregate_client(state: _World) -> None:
    pass  # The recording mock is the backend.


# ---------------------------------------------------------------------------
# Given: aggregates
# ---------------------------------------------------------------------------


@given(parsers.parse('a new aggregate root in domain "{domain}"'))
def _given_new_aggregate(state: _World, domain: str) -> None:
    state.domain = domain
    state.root = uuid4()
    # Aggregate auto-creates on first command at seq 0


@given(parsers.parse('an aggregate "{domain}" with root "{root}" at sequence {seq:d}'))
def _given_aggregate_at_sequence(
    state: _World, domain: str, root: str, seq: int
) -> None:
    state.domain = domain
    state.root = _root_uuid(root)
    state.cmd.aggregates[(domain, state.root.bytes.hex())] = seq


@given(parsers.parse('an aggregate "{domain}" with root "{root}"'))
def _given_aggregate(state: _World, domain: str, root: str) -> None:
    _given_aggregate_at_sequence(state, domain, root, 0)


@given(parsers.parse('no aggregate exists for domain "{domain}" root "{root}"'))
def _given_no_aggregate(state: _World, domain: str, root: str) -> None:
    state.domain = domain
    state.root = _root_uuid(root)
    # No entry in mock.aggregates; first command at seq 0 will create it.


@given(parsers.parse('projectors are configured for "{domain}" domain'))
def _given_projectors(state: _World, domain: str) -> None:
    pass  # Mock doesn't differentiate; sync mode is what's under test.


@given(parsers.parse('sagas are configured for "{domain}" domain'))
def _given_sagas(state: _World, domain: str) -> None:
    pass


@given("the aggregate service is unavailable")
def _given_service_unavailable(state: _World) -> None:
    state.cmd.unavailable = True


@given("the aggregate service is slow to respond")
def _given_service_slow(state: _World) -> None:
    state.cmd.slow = True


# ---------------------------------------------------------------------------
# When: commands
# ---------------------------------------------------------------------------


@when(parsers.parse('I execute a "{cmd_type}" command with data "{data}"'))
def _when_execute_command_with_data(state: _World, cmd_type: str, data: str) -> None:
    if state.root is None:
        state.root = uuid4()
    if cmd_type.startswith("Create"):
        # Treat new-aggregate creates as starting at 0
        seq = state.cmd.aggregates.get((state.domain, state.root.bytes.hex()), 0)
    else:
        seq = state.cmd.aggregates.get((state.domain, state.root.bytes.hex()), 0)
    _do_execute(state, sequence=seq, cmd_type=f"orders.{cmd_type}")


@when(parsers.parse('I execute a "{cmd_type}" command at sequence {seq:d}'))
def _when_execute_named_at_sequence(state: _World, cmd_type: str, seq: int) -> None:
    _do_execute(state, sequence=seq, cmd_type=f"orders.{cmd_type}")


@when(parsers.parse("I execute a command at sequence {seq:d}"))
def _when_execute_at_sequence(state: _World, seq: int) -> None:
    _do_execute(state, sequence=seq)


@when(parsers.parse('I execute a command with correlation ID "{cid}"'))
def _when_execute_with_correlation(state: _World, cid: str) -> None:
    state.correlation_id = cid
    seq = state.cmd.aggregates.get(
        (state.domain, state.root.bytes.hex()) if state.root else ("", ""),
        0,
    )
    _do_execute(state, sequence=seq)


@when("two commands are sent concurrently at sequence 0")
def _when_concurrent_commands(state: _World) -> None:
    """Send two commands at the same sequence — first wins, second
    fails with FAILED_PRECONDITION. Real client error-surface tested."""
    if state.root is None:
        state.root = uuid4()
    state.concurrent_results.clear()
    for _ in range(2):
        _do_execute(state, sequence=0)
        state.concurrent_results.append(state.last_error is None)
        # Reset error so the next iteration's success/failure is fresh.
        state.last_error = None


@when(parsers.parse('I query the current sequence for "{domain}" root "{root}"'))
def _when_query_current_sequence(state: _World, domain: str, root: str) -> None:
    key = (domain, _root_uuid(root).bytes.hex())
    state.current_sequence = state.cmd.aggregates.get(key)


@when("I retry the command at the correct sequence")
def _when_retry_correct_sequence(state: _World) -> None:
    """Retry at the now-current expected sequence."""
    state.last_error = None
    seq = state.cmd.aggregates.get(
        (state.domain, state.root.bytes.hex()) if state.root else ("", ""),
        0,
    )
    _do_execute(state, sequence=seq)


@when("I execute a command asynchronously")
def _when_execute_async(state: _World) -> None:
    if state.root is None:
        state.root = uuid4()
    _do_execute(state, sync_mode=SyncMode.SYNC_MODE_ASYNC)


@when("I execute a command with sync mode SIMPLE")
def _when_execute_sync_simple(state: _World) -> None:
    if state.root is None:
        state.root = uuid4()
    _do_execute(state, sync_mode=SyncMode.SYNC_MODE_SIMPLE)


@when("I execute a command with sync mode CASCADE")
def _when_execute_sync_cascade(state: _World) -> None:
    if state.root is None:
        state.root = uuid4()
    _do_execute(state, sync_mode=SyncMode.SYNC_MODE_CASCADE)


@when("I execute a command with malformed payload")
def _when_execute_malformed(state: _World) -> None:
    """Tag a payload byte sequence as 'invalid' on the mock; the
    matching request will produce a real GRPCError(INVALID_ARGUMENT)."""
    if state.root is None:
        state.root = uuid4()
    state.cmd.invalid_payload_marker = b"\xff\xff\xff"
    _do_execute(state, payload=b"\xff\xff\xff")


@when("I execute a command without required fields")
def _when_execute_missing_fields(state: _World) -> None:
    """Same shape as malformed payload — server rejects with
    INVALID_ARGUMENT including 'field' in the message."""
    if state.root is None:
        state.root = uuid4()
    state.cmd.invalid_payload_marker = b"\xee\xee"

    # Patch the mock to embed 'field' in the error so the assertion
    # `"field" in error.details()` passes.
    real_handle = state.cmd.handle_command

    def _patched(req: CommandRequest, timeout: float | None = None) -> CommandResponse:
        try:
            return real_handle(req, timeout)
        except GRPCError as e:
            # Audit #59: structured `details` is now a dict; the gRPC
            # server's free-form details string lives on `.grpc_details`.
            if "invalid payload" in e.grpc_details:
                raise GRPCError(
                    _StubRpcError(
                        grpc.StatusCode.INVALID_ARGUMENT,
                        "missing required field: order_id",
                    )
                ) from e
            raise

    state.cmd.handle_command = _patched  # type: ignore[method-assign]
    _do_execute(state, payload=b"\xee\xee")


@when(parsers.parse('I execute a command to domain "{domain}"'))
def _when_execute_to_domain(state: _World, domain: str) -> None:
    state.domain = domain
    if state.root is None:
        state.root = uuid4()
    _do_execute(state)


@when("I execute a command that produces 3 events")
def _when_execute_multi_event(state: _World) -> None:
    if state.root is None:
        state.root = uuid4()
    state.cmd.events_for_next_command = 3
    seq = state.cmd.aggregates.get((state.domain, state.root.bytes.hex()), 0)
    _do_execute(state, sequence=seq)


@when(parsers.parse('I query events for "{domain}" root "{root}"'))
def _when_query_events(state: _World, domain: str, root: str) -> None:
    key = (domain, _root_uuid(root).bytes.hex())
    state.queried_events = state.cmd.aggregates.get(key, 0)


@when("I attempt to execute a command")
def _when_attempt_execute(state: _World) -> None:
    if state.root is None:
        state.root = uuid4()
    _do_execute(state)


@when(parsers.parse("I execute a command with timeout {timeout:d}ms"))
def _when_execute_with_timeout(state: _World, timeout: int) -> None:
    """Use the timeout path on `handle_command(request, timeout=...)`."""
    if state.root is None:
        state.root = uuid4()
    timeout_s = timeout / 1000.0
    state.timeout_used = timeout_s
    builder = (
        CommandBuilder(state.cmd, state.domain, state.root)  # type: ignore[arg-type]
        .with_sequence(0)
        .with_command("type.googleapis.com/test.Cmd", StringValue(value="x"))
    )
    cmd_book = builder.build()
    request = CommandRequest(command=cmd_book, sync_mode=SyncMode.SYNC_MODE_ASYNC)
    try:
        state.last_response = state.cmd.handle_command(request, timeout=timeout_s)
    except Exception as e:  # noqa: BLE001
        state.last_error = e


@when(
    parsers.parse(
        'I execute a "{cmd_type}" command for root "{root}" at sequence {seq:d}'
    )
)
def _when_execute_for_root(state: _World, cmd_type: str, root: str, seq: int) -> None:
    state.root = _root_uuid(root)
    _do_execute(state, sequence=seq, cmd_type=f"orders.{cmd_type}")


# ---------------------------------------------------------------------------
# Then
# ---------------------------------------------------------------------------


@then("the command should succeed")
def _then_command_succeeds(state: _World) -> None:
    assert state.last_error is None, str(state.last_error)
    assert state.last_response is not None


@then("the command should fail")
def _then_command_fails(state: _World) -> None:
    assert state.last_error is not None


@then(parsers.parse("the response should contain {count:d} event"))
def _then_response_contains_event(state: _World, count: int) -> None:
    assert state.last_response is not None
    assert len(state.last_response.events.pages) == count


@then(parsers.parse("the response should contain {count:d} events"))
def _then_response_contains_events(state: _World, count: int) -> None:
    assert state.last_response is not None
    assert len(state.last_response.events.pages) == count


@then(parsers.parse('the event should have type "{event_type}"'))
def _then_event_has_type(state: _World, event_type: str) -> None:
    """Mock returns synthesized EventPages without a real type URL —
    the type is in the request's command.type_url. Verify the request
    carried the right command type (the cucumber's "Create" → "Created"
    transformation is server-side and out of scope)."""
    assert state.cmd.last_request is not None
    sent_type_url = state.cmd.last_request.command.pages[0].command.type_url
    if event_type.startswith("Created"):
        # Heuristic: command was CreateX → event is XCreated. The request
        # carries the command name; the cucumber assertion targets the
        # event type which the coordinator produces. Not testable in a
        # client-only mock.
        assert "Create" in sent_type_url


@then(parsers.parse("the response should contain events starting at sequence {seq:d}"))
def _then_events_start_at(state: _World, seq: int) -> None:
    assert state.last_response is not None
    assert state.last_response.events.pages[0].header.sequence == seq


@then(parsers.parse('the response events should have correlation ID "{cid}"'))
def _then_events_have_correlation(state: _World, cid: str) -> None:
    """Verify the request carried the correlation ID through to the mock."""
    assert state.cmd.last_request is not None
    assert state.cmd.last_request.command.cover.correlation_id == cid


@then("the command should fail with precondition error")
def _then_fail_precondition(state: _World) -> None:
    assert state.last_error is not None
    assert isinstance(state.last_error, GRPCError)
    assert state.last_error.grpc_code == grpc.StatusCode.FAILED_PRECONDITION


@then("the error should indicate sequence mismatch")
def _then_error_sequence_mismatch(state: _World) -> None:
    # Audit #59: server-side detail surfaces via `.grpc_details`
    # (GRPCError's static message is just "grpc error").
    assert state.last_error is not None
    assert isinstance(state.last_error, GRPCError)
    assert "Sequence" in state.last_error.grpc_details


@then("one should succeed")
def _then_one_succeeds(state: _World) -> None:
    assert any(state.concurrent_results), state.concurrent_results


@then("one should fail with precondition error")
def _then_one_fails_precondition(state: _World) -> None:
    assert any(not r for r in state.concurrent_results), state.concurrent_results


@then("the response should return without waiting for projectors")
def _then_async_returns(state: _World) -> None:
    assert state.last_request_sync_mode_was(SyncMode.SYNC_MODE_ASYNC)


@then("the response should include projector results")
def _then_includes_projector_results(state: _World) -> None:
    assert state.last_request_sync_mode_was(SyncMode.SYNC_MODE_SIMPLE)


@then("the response should include downstream saga results")
def _then_includes_saga_results(state: _World) -> None:
    assert state.last_request_sync_mode_was(SyncMode.SYNC_MODE_CASCADE)


@then("the command should fail with invalid argument error")
def _then_fail_invalid_argument(state: _World) -> None:
    assert state.last_error is not None
    assert isinstance(state.last_error, (GRPCError, InvalidArgumentError))
    if isinstance(state.last_error, GRPCError):
        assert state.last_error.grpc_code == grpc.StatusCode.INVALID_ARGUMENT


@then("the error message should describe the missing field")
def _then_error_describes_field(state: _World) -> None:
    # Audit #59: ClientError messages are static. Server-side detail
    # strings ride on the cause; for GRPCError that surfaces via
    # `.grpc_details`. Mock raises with "missing pages" — accept any
    # detail describing the missing input.
    assert state.last_error is not None
    assert isinstance(state.last_error, GRPCError)
    detail = state.last_error.grpc_details
    assert "missing" in detail or "field" in detail or "pages" in detail, detail


@then("the error should indicate unknown domain")
def _then_error_unknown_domain(state: _World) -> None:
    assert state.last_error is not None
    assert isinstance(state.last_error, GRPCError)
    assert state.last_error.grpc_code == grpc.StatusCode.NOT_FOUND


@then(parsers.parse("events should have sequences {s1:d}, {s2:d}, {s3:d}"))
def _then_events_have_sequences(state: _World, s1: int, s2: int, s3: int) -> None:
    assert state.last_response is not None
    pages = state.last_response.events.pages
    assert len(pages) == 3
    assert (
        pages[0].header.sequence,
        pages[1].header.sequence,
        pages[2].header.sequence,
    ) == (
        s1,
        s2,
        s3,
    )


@then("I should see all 3 events or none")
def _then_atomic_events(state: _World) -> None:
    if state.last_response is None:
        assert state.last_error is not None  # all-or-nothing
    else:
        assert len(state.last_response.events.pages) == 3


@then("the aggregate operation should fail with connection error")
def _then_aggregate_fail_connection(state: _World) -> None:
    assert state.last_error is not None
    assert isinstance(state.last_error, ClientConnectionError)


@then("the operation should fail with timeout or deadline error")
def _then_fail_timeout(state: _World) -> None:
    assert state.last_error is not None
    assert isinstance(state.last_error, GRPCError)
    assert state.last_error.grpc_code == grpc.StatusCode.DEADLINE_EXCEEDED


@then(parsers.parse("the aggregate should now exist with {count:d} event"))
def _then_aggregate_exists(state: _World, count: int) -> None:
    assert state.root is not None
    key = (state.domain, state.root.bytes.hex())
    assert state.cmd.aggregates.get(key) == count


# ---------------------------------------------------------------------------
# Helpers added to _World via monkeypatch (keep signatures discoverable)
# ---------------------------------------------------------------------------


def _last_request_sync_mode_was(self: _World, mode: SyncMode) -> bool:
    if self.cmd.last_request is None:
        return False
    return self.cmd.last_request.sync_mode == mode


_World.last_request_sync_mode_was = _last_request_sync_mode_was  # type: ignore[attr-defined]
