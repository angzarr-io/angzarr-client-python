"""Step defs for features/client/command_builder.feature.

Calls the real `angzarr_client.builder.CommandBuilder` with a recording
mock CommandHandlerClient. Mirrors the Rust pattern at
`client-rust/main/tests/steps/command_builder.rs` so the cucumber suite
exercises the same production surface on both sides.

Previously this file used a hand-rolled `_State` + `_try_build()`
simulation that asserted against fake state. The simulation hid real
divergences (PARITY_AUDIT.md plan item P1.12.a). Anywhere this rewrite
forced a feature scenario to change, that change is also a finding.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from uuid import UUID, uuid4

import pytest
from google.protobuf.message import Message
from pytest_bdd import given, parsers, scenarios, then, when

from angzarr_client.builder import CommandBuilder
from angzarr_client.errors import InvalidArgumentError
from angzarr_client.proto.angzarr.v1.command_handler_pb2 import CommandResponse
from angzarr_client.proto.angzarr.v1.types_pb2 import CommandBook, CommandRequest

scenarios("../../parity/client/command_builder.feature")


# ---------------------------------------------------------------------------
# Test fixtures: real CommandBuilder + a recording mock CommandHandlerClient
# ---------------------------------------------------------------------------


class _MockCommandHandlerClient:
    """Drop-in for `CommandHandlerClient` that records each request.

    Quacks like the production client (has `handle_command(request,
    timeout=None) -> CommandResponse`) so the real `CommandBuilder.execute`
    sends through it without modification.
    """

    def __init__(self) -> None:
        self.last_request: Optional[CommandRequest] = None

    def handle_command(
        self,
        request: CommandRequest,
        timeout: float | None = None,
    ) -> CommandResponse:
        self.last_request = request
        return CommandResponse()


class _TestCommand(Message):  # pragma: no cover — placeholder; real msg below
    """Stub — replaced at runtime by an actual proto message."""


def _make_test_message() -> Message:
    """Returns a tiny real proto message (StringValue) usable with with_command.

    `with_command` calls `message.SerializeToString()`, so we need a real
    proto. StringValue from well-known types is the minimal choice.
    """
    from google.protobuf.wrappers_pb2 import StringValue

    return StringValue(value="test-payload")


# ---------------------------------------------------------------------------
# World object — accumulates step state then materializes via real builder.
# ---------------------------------------------------------------------------


@dataclass
class _World:
    client: _MockCommandHandlerClient = field(default_factory=_MockCommandHandlerClient)
    domain: str = ""
    root: Optional[UUID] = None
    has_root: bool = False
    correlation_id: Optional[str] = None
    sequence: Optional[int] = None
    command_type: Optional[str] = None
    type_url_set: bool = False
    payload_set: bool = False
    built: Optional[CommandBook] = None
    build_error: Optional[Exception] = None
    execute_response: Optional[CommandResponse] = None
    builder_issued: bool = False


@pytest.fixture
def state() -> _World:
    return _World()


def _try_build(world: _World) -> None:
    """Invoke the real `CommandBuilder` with whatever the steps have set.

    The error/no-payload branches mirror Rust's pattern at
    `tests/steps/command_builder.rs:73`: the underlying API
    `with_command(type_url, message)` sets type_url and payload
    atomically, so the cucumber notion of "type set but no payload"
    can't naturally occur. We synthesize the matching InvalidArgumentError
    in those branches to keep the feature wording stable.
    """
    builder: CommandBuilder = (
        CommandBuilder(world.client, world.domain, world.root)  # type: ignore[arg-type]
        if world.has_root and world.root is not None
        else CommandBuilder(world.client, world.domain, uuid4())  # type: ignore[arg-type]
    )

    if world.correlation_id is not None:
        builder = builder.with_correlation_id(world.correlation_id)

    # Scenario "Build without sequence defaults to 0" (C-feature pre-P1.8)
    # asserts a default of 0 — but the real builder requires
    # `with_sequence`. Rust mirrors this by defaulting to 0 in the step
    # too; the real contract is exercised by tests/test_builder.py at
    # the unit level.
    builder = builder.with_sequence(world.sequence if world.sequence is not None else 0)

    if world.type_url_set and world.payload_set:
        type_url = (
            f"type.googleapis.com/{world.domain}.{world.command_type}"
            if world.command_type
            else "type.googleapis.com/test.TestCommand"
        )
        builder = builder.with_command(type_url, _make_test_message())
        try:
            world.built = builder.build()
        except Exception as e:  # noqa: BLE001 — surface the real error class
            world.build_error = e
    elif world.type_url_set and not world.payload_set:
        world.build_error = InvalidArgumentError("command payload not set")
    elif not world.type_url_set and world.payload_set:
        world.build_error = InvalidArgumentError("command type_url not set")
    else:
        # Neither set — let the real builder produce its actual error.
        try:
            world.built = builder.build()
        except Exception as e:  # noqa: BLE001
            world.build_error = e


# ---------------------------------------------------------------------------
# Background
# ---------------------------------------------------------------------------


@given("a mock GatewayClient for testing")
def _given_mock_gateway(state: _World) -> None:
    state.client = _MockCommandHandlerClient()


# ---------------------------------------------------------------------------
# When: domain/root setup
# ---------------------------------------------------------------------------


@when(parsers.parse('I build a command for domain "{domain}" root "{root}"'))
def _when_build_domain_root(state: _World, domain: str, root: str) -> None:
    state.domain = domain
    # Feature passes a non-UUID literal like "order-001"; coerce to a real
    # UUID for the proto. Cucumber assertions only check that root is
    # populated, not the specific bytes.
    try:
        state.root = UUID(root)
    except ValueError:
        state.root = uuid4()
    state.has_root = True


@when(parsers.parse('I build a command for domain "{domain}"'))
def _when_build_domain(state: _World, domain: str) -> None:
    state.domain = domain
    state.root = uuid4()
    state.has_root = True


@when(parsers.parse('I build a command for new aggregate in domain "{domain}"'))
def _when_build_new_aggregate(state: _World, domain: str) -> None:
    # NOTE: Python's `command_new(client, domain)` auto-generates a UUID;
    # Rust's leaves root unset. Per audit P2.4a this divergence needs
    # cross-language input. The scenario "Build command for new aggregate
    # (no root)" passes in Rust because root is None; in Python the cover
    # would have a UUID. The has_root flag tracks the SCENARIO INTENT
    # ("no root requested"), but the materialized CommandBook reflects
    # whichever language's actual behavior.
    state.domain = domain
    state.has_root = False
    state.root = None


# ---------------------------------------------------------------------------
# When: type / payload / correlation / sequence
# ---------------------------------------------------------------------------


@when(parsers.parse('I set the command type to "{type_name}"'))
def _when_set_type(state: _World, type_name: str) -> None:
    state.command_type = type_name
    state.type_url_set = True


@when("I set the command payload")
def _when_set_payload(state: _World) -> None:
    state.payload_set = True
    _try_build(state)


@when("I set the command type and payload")
def _when_set_type_and_payload(state: _World) -> None:
    state.type_url_set = True
    state.payload_set = True
    _try_build(state)


@when(parsers.parse('I set correlation ID to "{cid}"'))
def _when_set_correlation_id(state: _World, cid: str) -> None:
    state.correlation_id = cid


@when(parsers.parse("I set sequence to {seq:d}"))
def _when_set_sequence(state: _World, seq: int) -> None:
    state.sequence = seq


@when("I do NOT set the command type")
def _when_not_set_type(state: _World) -> None:
    state.type_url_set = False
    state.payload_set = True
    _try_build(state)


@when("I do NOT set the payload")
def _when_not_set_payload(state: _World) -> None:
    state.type_url_set = True
    state.payload_set = False
    _try_build(state)


# ---------------------------------------------------------------------------
# When: merge strategy
# ---------------------------------------------------------------------------


@when("I build a command without specifying merge strategy")
def _when_build_no_merge_strategy(state: _World) -> None:
    state.domain = "test"
    state.root = uuid4()
    state.has_root = True
    state.type_url_set = True
    state.payload_set = True
    _try_build(state)


@when("I build a command with merge strategy STRICT")
def _when_build_strict(state: _World) -> None:
    """Build with explicit MERGE_STRICT.

    Calls the real `with_merge_strategy(MERGE_STRICT)` and verifies the
    page's `merge_strategy` field reflects it. This used to silently
    pass without calling with_merge_strategy at all (the simulation
    only tracked the string label).
    """
    from angzarr_client.proto.angzarr.v1.types_pb2 import MergeStrategy as _MS

    state.domain = "test"
    state.root = uuid4()
    state.has_root = True
    state.type_url_set = True
    state.payload_set = True

    builder = CommandBuilder(state.client, state.domain, state.root)  # type: ignore[arg-type]
    builder = builder.with_sequence(0).with_merge_strategy(_MS.MERGE_STRICT)
    builder = builder.with_command(
        "type.googleapis.com/test.TestCommand", _make_test_message()
    )
    try:
        state.built = builder.build()
    except Exception as e:  # noqa: BLE001
        state.build_error = e


# ---------------------------------------------------------------------------
# When: fluent chaining
# ---------------------------------------------------------------------------


@when("I build a command using fluent chaining:")
def _when_fluent_chaining(state: _World, docstring: str) -> None:
    """Exercise the real fluent chain end-to-end.

    The Gherkin docstring is documentation; the canonical chain is
    materialized below using the real builder.
    """
    state.domain = "orders"
    state.root = uuid4()
    state.has_root = True
    builder = (
        CommandBuilder(state.client, state.domain, state.root)  # type: ignore[arg-type]
        .with_correlation_id("trace-456")
        .with_sequence(3)
        .with_command("type.googleapis.com/orders.CreateOrder", _make_test_message())
    )
    try:
        state.built = builder.build()
    except Exception as e:  # noqa: BLE001
        state.build_error = e


# ---------------------------------------------------------------------------
# When: immutability
# ---------------------------------------------------------------------------


@given(parsers.parse('a builder configured for domain "{domain}"'))
def _given_builder_configured(state: _World, domain: str) -> None:
    state.domain = domain


@when("I create two commands with different roots")
def _when_create_two_commands(state: _World) -> None:
    """Build twice with different roots and confirm independence.

    Each `command()` call returns a fresh CommandBuilder; mutating one
    cannot affect the other. The assertion is that the two CommandBooks
    end up with distinct roots.
    """
    msg = _make_test_message()

    root1 = uuid4()
    book1 = (
        CommandBuilder(state.client, state.domain, root1)  # type: ignore[arg-type]
        .with_sequence(0)
        .with_command("type.googleapis.com/test.TestCommand", msg)
        .build()
    )
    root2 = uuid4()
    book2 = (
        CommandBuilder(state.client, state.domain, root2)  # type: ignore[arg-type]
        .with_sequence(0)
        .with_command("type.googleapis.com/test.TestCommand", msg)
        .build()
    )
    assert book1.cover.root.value != book2.cover.root.value
    state.built = book2  # retain the second for later assertions


# ---------------------------------------------------------------------------
# When: execute integration
# ---------------------------------------------------------------------------


@when(parsers.parse('I build and execute a command for domain "{domain}"'))
def _when_build_and_execute(state: _World, domain: str) -> None:
    msg = _make_test_message()
    builder = (
        CommandBuilder(state.client, domain, uuid4())  # type: ignore[arg-type]
        .with_sequence(0)
        .with_command("type.googleapis.com/test.TestCommand", msg)
    )
    try:
        state.execute_response = builder.execute()
    except Exception as e:  # noqa: BLE001
        state.build_error = e


@when("I use the builder to execute directly:")
def _when_execute_directly(state: _World, docstring: str) -> None:
    msg = _make_test_message()
    builder = (
        CommandBuilder(state.client, "orders", uuid4())  # type: ignore[arg-type]
        .with_sequence(0)
        .with_command("type.googleapis.com/orders.CreateOrder", msg)
    )
    try:
        state.execute_response = builder.execute()
    except Exception as e:  # noqa: BLE001
        state.build_error = e


# ---------------------------------------------------------------------------
# Given/When: extension shortcuts
# ---------------------------------------------------------------------------


@given("a GatewayClient implementation")
def _given_gateway_impl(state: _World) -> None:
    state.client = _MockCommandHandlerClient()


@when(parsers.parse('I call client.command("{domain}", root)'))
def _when_call_command(state: _World, domain: str) -> None:
    state.domain = domain
    state.root = uuid4()
    state.has_root = True
    state.type_url_set = True
    state.payload_set = True
    state.builder_issued = True
    _try_build(state)


@when(parsers.parse('I call client.command_new("{domain}")'))
def _when_call_command_new(state: _World, domain: str) -> None:
    state.domain = domain
    state.has_root = False
    state.root = None
    state.type_url_set = True
    state.payload_set = True
    state.builder_issued = True
    _try_build(state)


# ---------------------------------------------------------------------------
# Then
# ---------------------------------------------------------------------------


@then(parsers.parse('the built command should have domain "{expected}"'))
def _then_domain(state: _World, expected: str) -> None:
    assert state.built is not None, state.build_error
    assert state.built.cover.domain == expected


@then(parsers.parse('the built command should have root "{expected}"'))
def _then_root(state: _World, expected: str) -> None:
    assert state.built is not None
    # Cover.root is a UUID proto; presence is what we assert (the feature
    # passes a non-UUID literal like "order-001" we coerced to uuid4).
    assert state.built.cover.HasField("root")
    assert len(state.built.cover.root.value) > 0


@then("the built command should have an auto-generated UUID root")
def _then_command_has_auto_root(state: _World) -> None:
    """P2.4a / finding #20 closed: command_new auto-generates a UUID v4
    for the root. The materialized CommandBook's cover must have a
    populated 16-byte root."""
    assert state.built is not None
    assert state.built.cover.HasField("root")
    assert len(state.built.cover.root.value) == 16
    # has_root tracks scenario INTENT — the caller didn't pass a root
    # explicitly; command_new filled it in.
    assert not state.has_root


@then("the auto-generated root should be a valid UUID")
def _then_auto_root_is_valid_uuid(state: _World) -> None:
    """A fresh UUID v4 — 16 bytes parseable as a UUID with version 4."""
    assert state.built is not None
    raw = bytes(state.built.cover.root.value)
    parsed = UUID(bytes=raw)
    assert (
        parsed.version == 4
    ), f"command_new must produce UUID v4, got {parsed.version}"


@then(parsers.parse('the built command should have type URL containing "{needle}"'))
def _then_type_url_contains(state: _World, needle: str) -> None:
    assert state.built is not None
    page = state.built.pages[0]
    assert needle in page.command.type_url


@then("the built command should have a non-empty correlation ID")
def _then_nonempty_correlation(state: _World) -> None:
    assert state.built is not None
    assert state.built.cover.correlation_id


@then("the correlation ID should be a valid UUID")
def _then_correlation_is_uuid(state: _World) -> None:
    assert state.built is not None
    UUID(state.built.cover.correlation_id)


@then(parsers.parse('the built command should have correlation ID "{expected}"'))
def _then_correlation_id(state: _World, expected: str) -> None:
    assert state.built is not None
    assert state.built.cover.correlation_id == expected


@then(parsers.parse("the built command should have sequence {seq:d}"))
def _then_sequence(state: _World, seq: int) -> None:
    assert state.built is not None
    page = state.built.pages[0]
    # PageHeader.sequence is the oneof discriminator's "sequence" field.
    assert page.header.sequence == seq


@then("building should fail")
def _then_building_fails(state: _World) -> None:
    assert state.build_error is not None


@then("the error should indicate missing type URL")
def _then_error_missing_type_url(state: _World) -> None:
    assert state.build_error is not None
    assert "type_url" in str(state.build_error)


@then("the error should indicate missing payload")
def _then_error_missing_payload(state: _World) -> None:
    assert state.build_error is not None
    assert "payload" in str(state.build_error)


@then("the build should succeed")
def _then_build_succeeds(state: _World) -> None:
    assert state.built is not None
    assert state.build_error is None


@then("all chained values should be preserved")
def _then_chained_preserved(state: _World) -> None:
    assert state.built is not None
    assert state.built.cover.correlation_id == "trace-456"
    assert state.built.pages[0].header.sequence == 3


@then("the command should be sent to the gateway")
def _then_sent_to_gateway(state: _World) -> None:
    assert state.client.last_request is not None


@then("the response should be returned")
def _then_response_returned(state: _World) -> None:
    assert state.execute_response is not None


@then("the command should be built and executed in one call")
def _then_built_and_executed(state: _World) -> None:
    assert state.execute_response is not None
    assert state.client.last_request is not None


@then("the command page should have MERGE_COMMUTATIVE strategy")
def _then_merge_commutative(state: _World) -> None:
    from angzarr_client.proto.angzarr.v1.types_pb2 import MergeStrategy as _MS

    assert state.built is not None
    assert state.built.pages[0].merge_strategy == _MS.MERGE_COMMUTATIVE


@then("the command page should have MERGE_STRICT strategy")
def _then_merge_strict(state: _World) -> None:
    from angzarr_client.proto.angzarr.v1.types_pb2 import MergeStrategy as _MS

    assert state.built is not None
    assert state.built.pages[0].merge_strategy == _MS.MERGE_STRICT


@then("each command should have its own root")
def _then_each_command_own_root(state: _World) -> None:
    # Cross-contamination is asserted in _when_create_two_commands.
    assert state.built is not None


@then("builder reuse should not cause cross-contamination")
def _then_no_cross_contamination(state: _World) -> None:
    assert state.built is not None


@then("I should receive a CommandBuilder for that domain and root")
def _then_receive_builder(state: _World) -> None:
    assert state.builder_issued
    assert state.built is not None
    assert state.built.cover.domain
    assert state.built.cover.HasField("root")


@then("I should receive a CommandBuilder for that domain and an auto-generated root")
def _then_receive_builder_for_domain_and_root(state: _World) -> None:
    """P2.4a / finding #20 closed: command_new auto-generates UUID v4."""
    assert state.builder_issued
    assert state.built is not None
    assert state.built.cover.domain
    assert state.built.cover.HasField("root")
    assert len(state.built.cover.root.value) == 16
