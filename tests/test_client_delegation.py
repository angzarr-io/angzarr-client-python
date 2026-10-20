"""Request shaping, deadline propagation, and error translation in clients."""

from unittest.mock import Mock
from uuid import UUID

import pytest

from angzarr_client._pb import CommandBook, CommandPage, Cover, Query
from angzarr_client.client import (
    CommandHandlerClient,
    DomainClient,
    QueryClient,
    _invoke,
)
from angzarr_client.errors import TransportError
from angzarr_client.helpers import uuid_to_proto

CORRELATION = UUID("0192f0c1-0000-7000-8000-000000000001")


class RecordingRpc:
    """Callable stand-in for a stub method; records request and deadline."""

    def __init__(self, response=None) -> None:
        self.response = response
        self.calls: list[tuple] = []

    def __call__(self, request, *, timeout, metadata):
        self.calls.append((request, timeout))
        return self.response


def _command(domain: str = "orders") -> CommandBook:
    return CommandBook(
        cover=Cover(domain=domain),
        page=CommandPage(correlation_id=uuid_to_proto(CORRELATION)),
    )


class TestInvoke:
    def test_stream_response_is_drained_into_list(self) -> None:
        books = ["a", "b"]
        rpc = RecordingRpc(iter(books))
        result = _invoke(rpc, Query(), timeout=None, metadata=(), stream=True)
        assert result == books

    def test_closed_channel_becomes_transport_error(self) -> None:
        cause = ValueError("Cannot invoke RPC on closed channel!")

        def rpc(request, *, timeout, metadata):
            raise cause

        with pytest.raises(TransportError) as info:
            _invoke(rpc, Query(), timeout=None, metadata=())

        assert info.value.cause is cause
        assert info.value.details == {"reason": "channel closed"}
        assert info.value.is_connection_error()

    def test_other_value_errors_propagate_unchanged(self) -> None:
        def rpc(request, *, timeout, metadata):
            raise ValueError("bad request")

        with pytest.raises(ValueError, match="bad request"):
            _invoke(rpc, Query(), timeout=None, metadata=())


class TestQueryClientCalls:
    def test_get_event_book_passes_deadline(self) -> None:
        stub = Mock(GetEventBook=RecordingRpc("book"))
        QueryClient.from_stub(stub).get_event_book(Query(), timeout=2.5)
        assert stub.GetEventBook.calls[0][1] == 2.5

    def test_get_events_drains_stream_and_passes_deadline(self) -> None:
        stub = Mock(GetEvents=RecordingRpc(iter(["b1", "b2"])))
        result = QueryClient.from_stub(stub).get_events(Query(), timeout=4.0)
        assert result == ["b1", "b2"]
        assert stub.GetEvents.calls[0][1] == 4.0


class TestCommandHandlerClientCalls:
    def test_handle_sends_the_book_itself_to_handle(self) -> None:
        # DECISION: the CommandBook is the request, with no wrapper.
        stub = Mock(Handle=RecordingRpc("resp"))
        cmd = _command()

        result = CommandHandlerClient.from_stub(stub).handle(cmd, timeout=3.0)

        assert result == "resp"
        request, timeout = stub.Handle.calls[0]
        assert request == cmd
        assert timeout == 3.0

    def test_offers_no_speculative_execution(self) -> None:
        # What-if execution is an edition, never a speculative RPC.
        assert not hasattr(CommandHandlerClient, "handle_sync_speculative")

    def test_command_new_builder_executes_through_this_client(self) -> None:
        stub = Mock(Handle=RecordingRpc("resp"))
        client = CommandHandlerClient.from_stub(stub)

        builder = client.command_new("orders").with_type_url("t").with_payload(b"p")
        assert builder.execute() == "resp"

        request = stub.Handle.calls[0][0]
        assert request.cover.domain == "orders"
        assert UUID(bytes=request.cover.root.value).version == 4


class RecordingQueryClient:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def get_event_book(self, query, timeout=None):
        self.calls.append(("book", query, timeout))
        return "book"

    def get_events(self, query, timeout=None):
        self.calls.append(("events", query, timeout))
        return ["events"]


class TestDomainClientDelegation:
    @pytest.fixture
    def handler_stub(self):
        return Mock(Handle=RecordingRpc("resp"))

    @pytest.fixture
    def client(self, handler_stub):
        return DomainClient.from_clients(
            CommandHandlerClient.from_stub(handler_stub),
            RecordingQueryClient(),
        )

    def test_execute_sends_the_book_to_handle(self, client, handler_stub) -> None:
        cmd = _command()
        assert client.execute(cmd, timeout=6.0) == "resp"

        request, timeout = handler_stub.Handle.calls[0]
        assert request == cmd
        assert timeout == 6.0

    def test_get_event_book_delegates_query_and_deadline(self, client) -> None:
        query = Query(cover=Cover(domain="orders"))
        assert client.get_event_book(query, timeout=8.0) == "book"
        assert client.query.calls == [("book", query, 8.0)]

    def test_get_events_delegates_query_and_deadline(self, client) -> None:
        query = Query(cover=Cover(domain="orders"))
        assert client.get_events(query, timeout=9.0) == ["events"]
        assert client.query.calls == [("events", query, 9.0)]
