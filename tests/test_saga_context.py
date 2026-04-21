"""Tests for angzarr_client.saga_context."""

from angzarr_client.destinations import Destinations
from angzarr_client.saga_context import SagaContext
from angzarr_client.proto.angzarr import types_pb2 as types


class TestSagaContext:
    def test_source_property(self) -> None:
        book = types.EventBook()
        book.cover.domain = "orders"
        ctx = SagaContext(book, {"inventory": 3})
        assert ctx.source is book

    def test_destinations_property(self) -> None:
        ctx = SagaContext(types.EventBook(), {"inventory": 3})
        assert isinstance(ctx.destinations, Destinations)
        assert ctx.destinations.sequence_for("inventory") == 3

    def test_sequence_for_delegates(self) -> None:
        ctx = SagaContext(types.EventBook(), {"inventory": 42})
        assert ctx.sequence_for("inventory") == 42
        assert ctx.sequence_for("unknown") is None

    def test_stamp_command_delegates(self) -> None:
        ctx = SagaContext(types.EventBook(), {"inventory": 9})
        cmd = types.CommandBook()
        cmd.pages.add().header.sequence = 0
        result = ctx.stamp_command(cmd, "inventory")
        assert result is cmd
        assert cmd.pages[0].header.sequence == 9

    def test_has_destination(self) -> None:
        ctx = SagaContext(types.EventBook(), {"inventory": 1})
        assert ctx.has_destination("inventory") is True
        assert ctx.has_destination("missing") is False
