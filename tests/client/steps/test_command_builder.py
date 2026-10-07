"""Step defs for parity/client/command_builder.feature.

Every scenario drives the real :class:`angzarr_client.builder.CommandBuilder`
obtained from a real :class:`CommandHandlerClient` (``command`` /
``command_new``) whose gRPC stub is a :class:`RecordingStub`. Steps record
only what the scenario sets into a recipe; the builder is then run with
exactly those calls, so its own defaults and validation decide the outcome.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace

import pytest
from google.protobuf.message import Message
from google.protobuf.wrappers_pb2 import StringValue
from pytest_bdd import given, parsers, scenarios, then, when

from angzarr_client._pb import CommandBook, CommandResponse, EventBook, MergeStrategy
from angzarr_client.builder import CommandBuilder
from angzarr_client.client import CommandHandlerClient
from angzarr_client.error_codes import codes
from angzarr_client.errors import ClientError
from angzarr_client.helpers import full_type_url
from tests.fixtures import CreateOrder

from ._fakes import RecordingStub

scenarios("parity/client/command_builder.feature")


TEST_COMMAND_URL = "/test.TestCommand"


def _root_for(label: str) -> uuid.UUID:
    """Deterministic root for a scenario label (cross-language byte parity)."""
    return uuid.uuid5(uuid.NAMESPACE_OID, label)


def _create_order() -> CreateOrder:
    return CreateOrder(order_id="o-1", customer_id="c-1")


def _test_command() -> StringValue:
    return StringValue(value="test")


@dataclass
class _Recipe:
    """What a scenario asked the builder to do."""

    domain: str = ""
    # None -> client.command_new (auto-generated root).
    root: uuid.UUID | None = None
    correlation_id: str | None = None
    sequence: int | None = None
    merge: int | None = None
    # (type_url, message) passed to with_command.
    command: tuple[str, Message] | None = None
    # Type URL set on its own via with_type_url.
    type_url: str | None = None
    # Encoded payload set on its own via with_payload.
    payload: bytes | None = None


def _apply(client: CommandHandlerClient, r: _Recipe) -> CommandBuilder:
    b = (
        client.command(r.domain, r.root)
        if r.root is not None
        else client.command_new(r.domain)
    )
    if r.correlation_id is not None:
        b = b.with_correlation_id(r.correlation_id)
    if r.sequence is not None:
        b = b.with_sequence(r.sequence)
    if r.merge is not None:
        b = b.with_merge_strategy(r.merge)
    if r.command is not None:
        b = b.with_command(*r.command)
    if r.type_url is not None:
        b = b.with_type_url(r.type_url)
    if r.payload is not None:
        b = b.with_payload(r.payload)
    return b


def _canned_response() -> CommandResponse:
    return CommandResponse(events=EventBook(next_sequence=42))


@dataclass
class _World:
    stub: RecordingStub = field(default_factory=RecordingStub)
    client: CommandHandlerClient | None = None
    recipe: _Recipe = field(default_factory=_Recipe)
    built: CommandBook | None = None
    build_error: Exception | None = None
    built_pair: list[CommandBook] = field(default_factory=list)
    pair_roots: list[uuid.UUID] = field(default_factory=list)
    executed: CommandResponse | None = None
    shortcut_root: uuid.UUID | None = None

    def build(self) -> None:
        assert self.client is not None, "a CommandHandlerClient was arranged"
        try:
            self.built = _apply(self.client, self.recipe).build()
            self.build_error = None
        except ClientError as e:
            self.built = None
            self.build_error = e

    def book(self) -> CommandBook:
        assert self.build_error is None, f"build failed: {self.build_error!r}"
        assert self.built is not None, "a command was built"
        return self.built


@pytest.fixture
def state() -> _World:
    return _World()


def _root_bytes(book: CommandBook) -> bytes:
    assert book.cover.HasField("root"), "root present"
    raw = bytes(book.cover.root.value)
    assert len(raw) == 16, "16-byte UUID root"
    return raw


# ---------------------------------------------------------------------------
# Arrangement
# ---------------------------------------------------------------------------


@given("a mock CommandHandlerClient for testing")
def _given_mock_client(state: _World) -> None:
    state.stub = RecordingStub()
    state.stub.responses["HandleCommand"] = _canned_response()
    state.client = CommandHandlerClient.from_stub(state.stub)


@given("a CommandHandlerClient implementation")
def _given_client_impl(state: _World) -> None:
    state.client = CommandHandlerClient.from_stub(RecordingStub())


@given(parsers.parse('a builder configured for domain "{domain}"'))
def _given_builder_configured(state: _World, domain: str) -> None:
    state.recipe.domain = domain


# ---------------------------------------------------------------------------
# Recipe steps
# ---------------------------------------------------------------------------


@when(parsers.parse('I build a command for domain "{domain}" root "{root}"'))
def _when_build_domain_root(state: _World, domain: str, root: str) -> None:
    state.recipe.domain = domain
    state.recipe.root = _root_for(root)


@when(parsers.parse('I build a command for domain "{domain}"'))
def _when_build_domain(state: _World, domain: str) -> None:
    state.recipe.domain = domain
    state.recipe.root = uuid.uuid4()


@when(parsers.parse('I build a command for new aggregate in domain "{domain}"'))
def _when_build_new_aggregate(state: _World, domain: str) -> None:
    state.recipe.domain = domain
    state.recipe.root = None


@when(parsers.parse('I set the command type to "{type_name}"'))
def _when_set_type(state: _World, type_name: str) -> None:
    assert type_name == "CreateOrder", "fixtures provide CreateOrder"
    state.recipe.type_url = full_type_url(CreateOrder)


@when("I set the command payload")
def _when_set_payload(state: _World) -> None:
    state.recipe.payload = _create_order().SerializeToString()
    state.build()


@when("I set the command type and payload")
def _when_set_type_and_payload(state: _World) -> None:
    state.recipe.command = (TEST_COMMAND_URL, _test_command())
    state.build()


@when(parsers.parse('I set correlation ID to "{cid}"'))
def _when_set_correlation_id(state: _World, cid: str) -> None:
    state.recipe.correlation_id = cid


@when(parsers.parse("I set sequence to {seq:d}"))
def _when_set_sequence(state: _World, seq: int) -> None:
    state.recipe.sequence = seq


@when("I do NOT set the command type")
def _when_not_set_type(state: _World) -> None:
    state.recipe.command = None
    state.recipe.type_url = None
    state.recipe.payload = _test_command().SerializeToString()
    state.build()


@when("I do NOT set the payload")
def _when_not_set_payload(state: _World) -> None:
    state.recipe.command = None
    state.recipe.payload = None
    state.build()


@when("I build a command without specifying merge strategy")
def _when_build_no_merge_strategy(state: _World) -> None:
    state.recipe = _Recipe(
        domain="orders",
        root=uuid.uuid4(),
        command=(TEST_COMMAND_URL, _test_command()),
    )
    state.build()


@when("I build a command with merge strategy STRICT")
def _when_build_strict(state: _World) -> None:
    state.recipe = _Recipe(
        domain="orders",
        root=uuid.uuid4(),
        merge=MergeStrategy.MERGE_STRICT,
        command=(TEST_COMMAND_URL, _test_command()),
    )
    state.build()


@when("I build a command using fluent chaining:")
def _when_fluent_chaining(state: _World) -> None:
    state.recipe = _Recipe(
        domain="orders",
        root=_root_for("order-chained"),
        correlation_id="trace-456",
        sequence=3,
        command=(full_type_url(CreateOrder), _create_order()),
    )
    state.build()


@when(parsers.parse('I build and execute a command for domain "{domain}"'))
def _when_build_and_execute(state: _World, domain: str) -> None:
    assert state.client is not None
    state.recipe = _Recipe(
        domain=domain,
        root=uuid.uuid4(),
        command=(TEST_COMMAND_URL, _test_command()),
    )
    state.build()
    state.executed = _apply(state.client, state.recipe).execute()


@when("I use the builder to execute directly:")
def _when_execute_directly(state: _World) -> None:
    assert state.client is not None
    state.recipe = _Recipe(
        domain="orders",
        root=uuid.uuid4(),
        command=(full_type_url(CreateOrder), _create_order()),
    )
    state.executed = _apply(state.client, state.recipe).execute()


@when("I create two commands with different roots")
def _when_create_two_commands(state: _World) -> None:
    assert state.client is not None
    roots = [_root_for("pair-a"), _root_for("pair-b")]
    for root in roots:
        recipe = replace(
            state.recipe, root=root, command=(TEST_COMMAND_URL, _test_command())
        )
        state.built_pair.append(_apply(state.client, recipe).build())
    state.pair_roots = roots


@when(parsers.parse('I call client.command("{domain}", root)'))
def _when_call_command(state: _World, domain: str) -> None:
    state.shortcut_root = _root_for("shortcut-root")
    state.recipe = _Recipe(
        domain=domain,
        root=state.shortcut_root,
        command=(TEST_COMMAND_URL, _test_command()),
    )
    state.build()


@when(parsers.parse('I call client.command_new("{domain}")'))
def _when_call_command_new(state: _World, domain: str) -> None:
    state.recipe = _Recipe(
        domain=domain,
        root=None,
        command=(TEST_COMMAND_URL, _test_command()),
    )
    state.build()


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------


@then(parsers.parse('the built command should have domain "{expected}"'))
def _then_domain(state: _World, expected: str) -> None:
    assert state.book().cover.domain == expected


@then(parsers.parse('the built command should have root "{expected}"'))
def _then_root(state: _World, expected: str) -> None:
    assert _root_bytes(state.book()) == _root_for(expected).bytes


@then("the built command should have an auto-generated UUID root")
def _then_command_has_auto_root(state: _World) -> None:
    assert state.recipe.root is None, "scenario used command_new"
    assert _root_bytes(state.book()) != bytes(16)


@then("the auto-generated root should be a valid UUID")
def _then_auto_root_is_valid_uuid(state: _World) -> None:
    parsed = uuid.UUID(bytes=_root_bytes(state.book()))
    assert (
        parsed.version == 4
    ), f"command_new must produce UUID v4, got {parsed.version}"


@then(parsers.parse('the built command should have type URL containing "{needle}"'))
def _then_type_url_contains(state: _World, needle: str) -> None:
    type_url = state.book().pages[0].command.type_url
    assert needle in type_url, f"type_url: {type_url}"
    assert type_url.startswith("/"), f"type_url: {type_url}"


@then("the built command should have a non-empty correlation ID")
def _then_nonempty_correlation(state: _World) -> None:
    assert state.book().cover.correlation_id != ""


@then("the correlation ID should be a valid UUID")
def _then_correlation_is_uuid(state: _World) -> None:
    uuid.UUID(state.book().cover.correlation_id)


@then(parsers.parse('the built command should have correlation ID "{expected}"'))
def _then_correlation_id(state: _World, expected: str) -> None:
    assert state.book().cover.correlation_id == expected


@then(parsers.parse("the built command should have sequence {seq:d}"))
def _then_sequence(state: _World, seq: int) -> None:
    header = state.book().pages[0].header
    assert header.WhichOneof("sequence_type") == "sequence"
    assert header.sequence == seq


@then("building should fail")
def _then_building_fails(state: _World) -> None:
    assert state.built is None, f"build unexpectedly succeeded: {state.built}"
    assert state.build_error is not None


@then("the error should indicate missing type URL")
def _then_error_missing_type_url(state: _World) -> None:
    assert isinstance(state.build_error, ClientError)
    assert state.build_error.code == codes.COMMAND_TYPE_URL_MISSING


@then("the error should indicate missing payload")
def _then_error_missing_payload(state: _World) -> None:
    assert isinstance(state.build_error, ClientError)
    assert state.build_error.code == codes.COMMAND_PAYLOAD_MISSING


@then("the build should succeed")
def _then_build_succeeds(state: _World) -> None:
    state.book()


@then("all chained values should be preserved")
def _then_chained_preserved(state: _World) -> None:
    book = state.book()
    assert book.cover.domain == "orders"
    assert _root_bytes(book) == _root_for("order-chained").bytes
    assert book.cover.correlation_id == "trace-456"
    assert book.pages[0].header.sequence == 3
    any_cmd = book.pages[0].command
    assert any_cmd.type_url == full_type_url(CreateOrder)
    decoded = CreateOrder()
    decoded.ParseFromString(any_cmd.value)
    assert decoded.customer_id == "c-1"


@then("the command should be sent to the gateway")
def _then_sent_to_gateway(state: _World) -> None:
    call = state.stub.last_call("HandleCommand")
    assert call is not None, "gateway received HandleCommand"
    sent = call[0].command
    built = state.book()
    # build() and execute() each mint a fresh correlation id, so compare
    # the addressing and the command itself.
    assert sent.cover.domain == built.cover.domain
    assert sent.cover.root == built.cover.root
    assert sent.cover.correlation_id != ""
    assert len(sent.pages) == 1
    assert sent.pages[0].command == built.pages[0].command
    assert sent.pages[0].header.sequence == built.pages[0].header.sequence


@then("the response should be returned")
def _then_response_returned(state: _World) -> None:
    assert state.executed == _canned_response()


@then("the command should be built and executed in one call")
def _then_built_and_executed(state: _World) -> None:
    assert state.executed == _canned_response()
    calls = [c for c in state.stub.calls if c[0] == "HandleCommand"]
    assert len(calls) == 1
    assert calls[0][1].command.pages[0].command.type_url == full_type_url(CreateOrder)


@then("the command page should have MERGE_COMMUTATIVE strategy")
def _then_merge_commutative(state: _World) -> None:
    assert state.book().pages[0].merge_strategy == MergeStrategy.MERGE_COMMUTATIVE


@then("the command page should have MERGE_STRICT strategy")
def _then_merge_strict(state: _World) -> None:
    assert state.book().pages[0].merge_strategy == MergeStrategy.MERGE_STRICT


@then("each command should have its own root")
def _then_each_command_own_root(state: _World) -> None:
    roots = [_root_bytes(b) for b in state.built_pair]
    assert roots == [r.bytes for r in state.pair_roots]


@then("builder reuse should not cause cross-contamination")
def _then_no_cross_contamination(state: _World) -> None:
    assert len(state.built_pair) == 2
    a, b = (book.cover for book in state.built_pair)
    assert a.domain == state.recipe.domain
    assert b.domain == state.recipe.domain
    assert (
        a.correlation_id != b.correlation_id
    ), "each build gets its own correlation id"


@then("I should receive a CommandBuilder for that domain and root")
def _then_receive_builder(state: _World) -> None:
    book = state.book()
    assert book.cover.domain == state.recipe.domain
    assert state.shortcut_root is not None
    assert _root_bytes(book) == state.shortcut_root.bytes


@then("I should receive a CommandBuilder for that domain and an auto-generated root")
def _then_receive_builder_for_domain_and_root(state: _World) -> None:
    book = state.book()
    assert book.cover.domain == state.recipe.domain
    assert uuid.UUID(bytes=_root_bytes(book)).version == 4
