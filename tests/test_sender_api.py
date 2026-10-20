"""Command senders: Handling (expected_sequence, conflict_resolution) on the
built command, and the command mode chosen by which method sends it — one
per CommandHandlerCoordinatorService RPC (command_handler.proto, the
command_modes region; aggregate_client.feature C-0197, C-0198):

- ``handle_async`` / ``execute_async``: ASYNC, fire and forget (None);
- ``handle`` / ``execute``: DECISION, the decision (CommandResponse);
- ``handle_sync`` / ``execute_sync``: SIMPLE, the decision plus the
  synchronous projectors' results (CommandResponse.outcome).
"""

from __future__ import annotations

import uuid

import pytest

from angzarr_client import CommandHandlerClient, ConflictResolution
from angzarr_client.proto.io.angzarr.v1 import command_handler_pb2, types_pb2

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


# --- command modes: one method per RPC ---


def test_c0197_a_fire_and_forget_command_returns_before_the_decision():
    client, coordinator = _client()
    assert client.handle_async(_book()) is None
    assert [rpc for rpc, _ in coordinator.calls] == ["HandleAsync"]


def test_c0198_waiting_for_projectors_returns_their_projections():
    client, coordinator = _client()
    response = client.handle_sync(_book())
    assert [rpc for rpc, _ in coordinator.calls] == ["HandleSync"]
    assert [p.projector for p in response.outcome.projections] == ["orders-view"]


def test_handle_waits_for_the_decision():
    client, coordinator = _client()
    client.handle(_book())
    assert [rpc for rpc, _ in coordinator.calls] == ["Handle"]


def _builder(client):
    return (
        client.command("orders", _ROOT)
        .with_type_url("/orders.v1.AddItem")
        .with_payload(b"")
    )


def test_the_builder_executes_in_each_mode():
    client, coordinator = _client()
    assert _builder(client).execute_async() is None
    _builder(client).execute()
    sync = _builder(client).execute_sync()
    assert [rpc for rpc, _ in coordinator.calls] == [
        "HandleAsync",
        "Handle",
        "HandleSync",
    ]
    assert [p.projector for p in sync.outcome.projections] == ["orders-view"]


def test_every_mode_carries_the_correlation_header():
    client, coordinator = _client()
    seen = []
    for rpc in ("Handle", "HandleAsync", "HandleSync"):
        original = getattr(coordinator, rpc)

        def record(book, original=original, **kw):
            seen.append(dict(kw.get("metadata") or ()))
            return original(book, **kw)

        setattr(coordinator, rpc, record)
    book = _builder(client).with_correlation_id(_ROOT).build()
    client.handle(book)
    client.handle_async(book)
    client.handle_sync(book)
    assert len(seen) == 3
    assert all(md.get("x-correlation-id") for md in seen)


# --- Handling on the sent command ---


def test_the_builder_sets_the_expected_sequence_and_conflict_resolution():
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
    assert not book.HasField("handling")


def test_the_domain_client_sends_in_each_mode():
    from angzarr_client import DomainClient

    client, coordinator = _client()
    domain = DomainClient.__new__(DomainClient)
    domain.command_handler = client
    assert domain.execute_async(_book()) is None
    domain.execute(_book())
    sync = domain.execute_sync(_book())
    assert [rpc for rpc, _ in coordinator.calls] == [
        "HandleAsync",
        "Handle",
        "HandleSync",
    ]
    assert [p.projector for p in sync.outcome.projections] == ["orders-view"]
