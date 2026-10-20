"""Item-4 proposal, command senders (docs/design/handling-and-command-modes.md):
how a sender sets Handling (expected_sequence, conflict_resolution) and
chooses the command mode — ASYNC (fire and forget), DECISION (wait for the
decision) or SIMPLE (wait for the synchronous projectors) — over
CommandHandlerCoordinatorService's three RPCs (command_handler.proto, the
command_modes region; aggregate_client.feature C-0197, C-0198).

Names come from the spec: ``Handling``, ``expected_sequence``,
``conflict_resolution``, ``ConflictResolution``, and the modes ASYNC,
DECISION and SIMPLE with the spec's ``handle(book, mode)`` sugar. Strict
xfails until the owner signs the proposal off: each fails today because the
proposed symbol is missing, and implementing one forces its marker off.
"""

from __future__ import annotations

import uuid

import pytest

from angzarr_client import CommandHandlerClient
from angzarr_client.proto.io.angzarr.v1 import command_handler_pb2, types_pb2

PENDING = pytest.mark.xfail(
    strict=True,
    reason="item-4 proposal (sender Handling and command modes) pending owner sign-off",
)

_ROOT = uuid.UUID("3cfe0a31-d58d-5190-af30-5459ccc221f7")


class _Coordinator:
    """A CommandHandlerCoordinatorService stub recording which RPC each
    command arrived on."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, types_pb2.CommandBook]] = []

    def _answer(self, rpc: str, book, **_kw):
        self.calls.append((rpc, book))
        if rpc == "HandleAsync":
            return None
        response = command_handler_pb2.CommandResponse()
        if rpc == "HandleSync":
            response.outcome.projections.add(projector="orders-view")
        return response

    def Handle(self, book, **kw):
        return self._answer("Handle", book, **kw)

    def HandleAsync(self, book, **kw):
        return self._answer("HandleAsync", book, **kw)

    def HandleSync(self, book, **kw):
        return self._answer("HandleSync", book, **kw)


def _client() -> tuple[CommandHandlerClient, _Coordinator]:
    coordinator = _Coordinator()
    return CommandHandlerClient.from_stub(coordinator), coordinator


def _book() -> types_pb2.CommandBook:
    book = types_pb2.CommandBook()
    book.cover.domain = "orders"
    book.page.command.payload.type_url = "/orders.v1.AddItem"
    return book


# --- command modes: handle(book, mode) ---


@PENDING
def test_c0197_a_fire_and_forget_command_returns_before_the_decision():
    from angzarr_client import CommandMode

    client, coordinator = _client()
    assert client.handle(_book(), mode=CommandMode.ASYNC) is None
    assert [rpc for rpc, _ in coordinator.calls] == ["HandleAsync"]


@PENDING
def test_c0198_waiting_for_projectors_returns_their_projections():
    from angzarr_client import CommandMode

    client, coordinator = _client()
    response = client.handle(_book(), mode=CommandMode.SIMPLE)
    assert [rpc for rpc, _ in coordinator.calls] == ["HandleSync"]
    assert [p.projector for p in response.outcome.projections] == ["orders-view"]


@PENDING
def test_the_default_mode_waits_for_the_decision():
    from angzarr_client import CommandMode

    client, coordinator = _client()
    client.handle(_book())
    client.handle(_book(), mode=CommandMode.DECISION)
    assert [rpc for rpc, _ in coordinator.calls] == ["Handle", "Handle"]


@PENDING
def test_the_builder_executes_in_the_chosen_mode():
    from angzarr_client import CommandMode

    client, coordinator = _client()
    client.command("orders", _ROOT).with_type_url("/orders.v1.AddItem").with_payload(
        b""
    ).execute(mode=CommandMode.ASYNC)
    assert [rpc for rpc, _ in coordinator.calls] == ["HandleAsync"]


# --- Handling on the sent command ---


@PENDING
def test_the_builder_sets_the_expected_sequence_and_conflict_resolution():
    from angzarr_client import ConflictResolution

    client, _ = _client()
    book = (
        client.command("orders", _ROOT)
        .with_type_url("/orders.v1.AddItem")
        .with_payload(b"")
        .with_expected_sequence(3)
        .with_conflict_resolution(ConflictResolution.CONFLICT_RESOLUTION_COMMUTATIVE)
        .build()
    )
    assert book.handling.HasField("expected_sequence")
    assert book.handling.expected_sequence == 3
    assert book.handling.conflict_resolution == (
        types_pb2.ConflictResolution.CONFLICT_RESOLUTION_COMMUTATIVE
    )


@PENDING
def test_an_expected_sequence_of_zero_is_sent_as_set():
    # expected_sequence is optional on the wire: 0 means "a new stream",
    # absent means "stamp the head" (types.proto, Handling).
    client, _ = _client()
    book = (
        client.command("orders", _ROOT)
        .with_type_url("/orders.v1.AddItem")
        .with_payload(b"")
        .with_expected_sequence(0)
        .build()
    )
    assert book.handling.HasField("expected_sequence")


def test_a_command_built_without_handling_leaves_it_to_the_coordinator():
    # The behaviour the proposal keeps: no Handling field is sent unless the
    # sender sets one, so the coordinator stamps and configures it.
    client, _ = _client()
    book = (
        client.command("orders", _ROOT)
        .with_type_url("/orders.v1.AddItem")
        .with_payload(b"")
        .build()
    )
    assert not book.handling.HasField("expected_sequence")
    assert book.handling.conflict_resolution == (
        types_pb2.ConflictResolution.CONFLICT_RESOLUTION_UNSPECIFIED
    )
