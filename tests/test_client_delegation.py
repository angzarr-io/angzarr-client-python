"""Request shaping, deadline propagation, and error translation in clients."""

from unittest.mock import Mock
from uuid import UUID

import grpc
import pytest

from angzarr_client._pb import (
    CascadeErrorMode,
    CommandBook,
    Cover,
    Query,
    SpeculateCommandHandlerRequest,
    SpeculatePmRequest,
    SpeculateProjectorRequest,
    SpeculateSagaRequest,
    SyncMode,
)
from angzarr_client.client import (
    CommandHandlerClient,
    DomainClient,
    QueryClient,
    SpeculativeClient,
    _invoke,
)
from angzarr_client.errors import TransportError


class RecordingRpc:
    """Callable stand-in for a stub method; records request and deadline."""

    def __init__(self, response=None) -> None:
        self.response = response
        self.calls: list[tuple] = []

    def __call__(self, request, *, timeout, metadata):
        self.calls.append((request, timeout))
        return self.response


def _command(domain: str = "orders") -> CommandBook:
    return CommandBook(cover=Cover(domain=domain, correlation_id="corr-1"))


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
    def test_handle_wraps_command_as_async_fail_fast(self) -> None:
        stub = Mock(HandleCommand=RecordingRpc("resp"))
        cmd = _command()

        result = CommandHandlerClient.from_stub(stub).handle(cmd, timeout=3.0)

        assert result == "resp"
        request, timeout = stub.HandleCommand.calls[0]
        assert request.command == cmd
        assert request.sync_mode == SyncMode.SYNC_MODE_ASYNC
        assert request.cascade_error_mode == CascadeErrorMode.CASCADE_ERROR_FAIL_FAST
        assert timeout == 3.0

    def test_handle_sync_speculative_passes_deadline(self) -> None:
        stub = Mock(HandleSyncSpeculative=RecordingRpc("resp"))
        CommandHandlerClient.from_stub(stub).handle_sync_speculative(
            SpeculateCommandHandlerRequest(), timeout=1.5
        )
        assert stub.HandleSyncSpeculative.calls[0][1] == 1.5

    def test_command_new_builder_executes_through_this_client(self) -> None:
        stub = Mock(HandleCommand=RecordingRpc("resp"))
        client = CommandHandlerClient.from_stub(stub)

        builder = client.command_new("orders").with_type_url("t").with_payload(b"p")
        assert builder.execute() == "resp"

        request = stub.HandleCommand.calls[0][0]
        assert request.command.cover.domain == "orders"
        assert UUID(bytes=request.command.cover.root.value).version == 4


class TestSpeculativeClientDeadlines:
    @pytest.fixture
    def stubs(self):
        return (
            Mock(HandleSyncSpeculative=RecordingRpc()),
            Mock(ExecuteSpeculative=RecordingRpc()),
            Mock(HandleSpeculative=RecordingRpc()),
            Mock(HandleSpeculative=RecordingRpc()),
        )

    def test_each_method_passes_deadline(self, stubs) -> None:
        ch, saga, proj, pm = stubs
        client = SpeculativeClient.from_stubs(ch, saga, proj, pm)

        client.command_handler(SpeculateCommandHandlerRequest(), timeout=1.0)
        client.saga(SpeculateSagaRequest(), timeout=2.0)
        client.projector(SpeculateProjectorRequest(), timeout=3.0)
        client.process_manager(SpeculatePmRequest(), timeout=4.0)

        assert ch.HandleSyncSpeculative.calls[0][1] == 1.0
        assert saga.ExecuteSpeculative.calls[0][1] == 2.0
        assert proj.HandleSpeculative.calls[0][1] == 3.0
        assert pm.HandleSpeculative.calls[0][1] == 4.0


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
        return Mock(HandleCommand=RecordingRpc("resp"))

    @pytest.fixture
    def client(self, handler_stub):
        return DomainClient.from_clients(
            CommandHandlerClient.from_stub(handler_stub),
            RecordingQueryClient(),
            Mock(spec=grpc.Channel),
        )

    def test_execute_sends_async_fail_fast_request(self, client, handler_stub) -> None:
        cmd = _command()
        assert client.execute(cmd, timeout=6.0) == "resp"

        request, timeout = handler_stub.HandleCommand.calls[0]
        assert request.command == cmd
        assert request.sync_mode == SyncMode.SYNC_MODE_ASYNC
        assert request.cascade_error_mode == CascadeErrorMode.CASCADE_ERROR_FAIL_FAST
        assert timeout == 6.0

    def test_execute_with_mode_uses_requested_mode(self, client, handler_stub) -> None:
        cmd = _command("inventory")
        client.execute_with_mode(cmd, SyncMode.SYNC_MODE_CASCADE, timeout=7.0)

        request, timeout = handler_stub.HandleCommand.calls[0]
        assert request.command == cmd
        assert request.sync_mode == SyncMode.SYNC_MODE_CASCADE
        assert request.cascade_error_mode == CascadeErrorMode.CASCADE_ERROR_FAIL_FAST
        assert timeout == 7.0

    def test_get_event_book_delegates_query_and_deadline(self, client) -> None:
        query = Query(cover=Cover(domain="orders"))
        assert client.get_event_book(query, timeout=8.0) == "book"
        assert client.query.calls == [("book", query, 8.0)]

    def test_get_events_delegates_query_and_deadline(self, client) -> None:
        query = Query(cover=Cover(domain="orders"))
        assert client.get_events(query, timeout=9.0) == ["events"]
        assert client.query.calls == [("events", query, 9.0)]
