"""Tests for client classes."""

import os
from unittest.mock import Mock, patch
from uuid import UUID

import grpc
import pytest

from angzarr_client._pb import (
    CommandBook,
    CommandPage,
    CommandResponse,
    EventBook,
    Query,
)
from angzarr_client.client import (
    CommandHandlerClient,
    DomainClient,
    QueryClient,
)
from angzarr_client.errors import GRPCError
from angzarr_client.helpers import uuid_to_proto

TRACE = "0192f0c1-0000-7000-8000-00000000c0de"


class MockRpcError(grpc.RpcError):
    """Mock RpcError for testing.

    grpc.RpcError itself doesn't have code/details methods - those come
    from grpc.Call. Real gRPC errors inherit from both.
    """

    def __init__(self, code: grpc.StatusCode, details: str = ""):
        super().__init__()
        self._code = code
        self._details = details

    def code(self) -> grpc.StatusCode:
        return self._code

    def details(self) -> str:
        return self._details


class TestQueryClient:
    """Tests for QueryClient."""

    def _mock_channel(self) -> Mock:
        """Create a mock gRPC channel."""
        return Mock(spec=grpc.Channel)

    def test_init_creates_stub(self) -> None:
        """Constructor creates stub from channel."""
        channel = self._mock_channel()
        client = QueryClient(channel)
        assert client._channel is channel
        assert client._stub is not None

    @patch("angzarr_client.client.grpc.insecure_channel")
    def test_connect(self, mock_channel: Mock) -> None:
        """connect creates client from endpoint."""
        mock_channel.return_value = Mock(spec=grpc.Channel)
        client = QueryClient.connect("localhost:9000")
        mock_channel.assert_called_once_with("localhost:9000")
        assert client is not None

    @patch.dict(os.environ, {"TEST_ENDPOINT": "env-host:9000"})
    @patch("angzarr_client.client.grpc.insecure_channel")
    def test_from_env_uses_env_var(self, mock_channel: Mock) -> None:
        """from_env uses environment variable."""
        mock_channel.return_value = Mock(spec=grpc.Channel)
        QueryClient.from_env("TEST_ENDPOINT", "default:8000")
        mock_channel.assert_called_once_with("env-host:9000")

    @patch("angzarr_client.client.grpc.insecure_channel")
    def test_from_env_uses_default(self, mock_channel: Mock) -> None:
        """from_env uses default when env var not set."""
        # Use a unique var name that won't exist, don't clear all env vars
        # (clearing all breaks mutmut which needs MUTANT_UNDER_TEST)
        mock_channel.return_value = Mock(spec=grpc.Channel)
        QueryClient.from_env("QUERY_CLIENT_NONEXISTENT_VAR_12345", "default:8000")
        mock_channel.assert_called_once_with("default:8000")

    def test_get_event_book_success(self) -> None:
        """get_event_book returns EventBook on success."""
        channel = self._mock_channel()
        client = QueryClient(channel)
        expected_book = EventBook()
        expected_book.next_sequence = 42
        client._stub.GetEventBook = Mock(return_value=expected_book)

        query = Query()
        result = client.get_event_book(query)

        client._stub.GetEventBook.assert_called_once_with(
            query, timeout=None, metadata=[]
        )
        assert result.next_sequence == 42

    def test_get_event_book_raises_grpc_error(self) -> None:
        """get_event_book raises GRPCError on RpcError."""
        channel = self._mock_channel()
        client = QueryClient(channel)
        rpc_error = MockRpcError(grpc.StatusCode.NOT_FOUND, "not found")
        client._stub.GetEventBook = Mock(side_effect=rpc_error)

        query = Query()
        with pytest.raises(GRPCError) as exc_info:
            client.get_event_book(query)
        assert exc_info.value.is_not_found()

    def test_get_events_success(self) -> None:
        """get_events returns list of EventBooks."""
        channel = self._mock_channel()
        client = QueryClient(channel)
        book1 = EventBook()
        book1.next_sequence = 1
        book2 = EventBook()
        book2.next_sequence = 2
        client._stub.GetEvents = Mock(return_value=[book1, book2])

        query = Query()
        result = client.get_events(query)

        assert len(result) == 2
        assert result[0].next_sequence == 1
        assert result[1].next_sequence == 2

    def test_get_events_raises_grpc_error(self) -> None:
        """get_events raises GRPCError on RpcError."""
        channel = self._mock_channel()
        client = QueryClient(channel)
        rpc_error = MockRpcError(grpc.StatusCode.INTERNAL)
        client._stub.GetEvents = Mock(side_effect=rpc_error)

        query = Query()
        with pytest.raises(GRPCError):
            client.get_events(query)

    def test_close_closes_channel(self) -> None:
        """close closes the underlying channel."""
        channel = self._mock_channel()
        client = QueryClient(channel)
        client.close()
        channel.close.assert_called_once()


class TestCommandHandlerClient:
    """Tests for CommandHandlerClient."""

    def _mock_channel(self) -> Mock:
        """Create a mock gRPC channel."""
        return Mock(spec=grpc.Channel)

    def test_init_creates_stub(self) -> None:
        """Constructor creates stub from channel."""
        channel = self._mock_channel()
        client = CommandHandlerClient(channel)
        assert client._channel is channel
        assert client._stub is not None

    @patch("angzarr_client.client.grpc.insecure_channel")
    def test_connect(self, mock_channel: Mock) -> None:
        """connect creates client from endpoint."""
        mock_channel.return_value = Mock(spec=grpc.Channel)
        CommandHandlerClient.connect("localhost:9000")
        mock_channel.assert_called_once_with("localhost:9000")

    @patch.dict(os.environ, {"AGG_ENDPOINT": "agg-host:9000"})
    @patch("angzarr_client.client.grpc.insecure_channel")
    def test_from_env_uses_env_var(self, mock_channel: Mock) -> None:
        """from_env uses environment variable."""
        mock_channel.return_value = Mock(spec=grpc.Channel)
        CommandHandlerClient.from_env("AGG_ENDPOINT", "default:8000")
        mock_channel.assert_called_once_with("agg-host:9000")

    def test_handle_success(self) -> None:
        """handle returns the CommandResponse of Handle (DECISION)."""
        channel = self._mock_channel()
        client = CommandHandlerClient(channel)
        expected_resp = CommandResponse()
        client._stub.Handle = Mock(return_value=expected_resp)

        cmd = CommandBook()
        result = client.handle(cmd)

        client._stub.Handle.assert_called_once_with(cmd, timeout=None, metadata=[])
        assert result is expected_resp

    def test_handle_raises_grpc_error(self) -> None:
        """handle raises GRPCError on RpcError."""
        channel = self._mock_channel()
        client = CommandHandlerClient(channel)
        rpc_error = MockRpcError(grpc.StatusCode.INTERNAL)
        client._stub.Handle = Mock(side_effect=rpc_error)

        with pytest.raises(GRPCError):
            client.handle(CommandBook())

    def test_close_closes_channel(self) -> None:
        """close closes the underlying channel."""
        channel = self._mock_channel()
        client = CommandHandlerClient(channel)
        client.close()
        channel.close.assert_called_once()


class TestDomainClient:
    """Tests for DomainClient."""

    def _mock_channel(self) -> Mock:
        """Create a mock gRPC channel."""
        return Mock(spec=grpc.Channel)

    def test_init_creates_sub_clients(self) -> None:
        """Constructor creates aggregate and query clients."""
        channel = self._mock_channel()
        client = DomainClient(channel)
        assert client._channel is channel
        assert client.command_handler is not None
        assert client.query is not None
        assert isinstance(client.command_handler, CommandHandlerClient)
        assert isinstance(client.query, QueryClient)

    @patch("angzarr_client.client.grpc.insecure_channel")
    def test_connect(self, mock_channel: Mock) -> None:
        """connect creates client from endpoint."""
        mock_channel.return_value = Mock(spec=grpc.Channel)
        DomainClient.connect("localhost:9000")
        mock_channel.assert_called_once_with("localhost:9000")

    @patch.dict(os.environ, {"DOMAIN_ENDPOINT": "domain-host:9000"})
    @patch("angzarr_client.client.grpc.insecure_channel")
    def test_from_env_uses_env_var(self, mock_channel: Mock) -> None:
        """from_env uses environment variable."""
        mock_channel.return_value = Mock(spec=grpc.Channel)
        DomainClient.from_env("DOMAIN_ENDPOINT", "default:8000")
        mock_channel.assert_called_once_with("domain-host:9000")

    def test_execute_delegates_to_command_handler(self) -> None:
        """execute delegates the book to command_handler.handle."""
        channel = self._mock_channel()
        client = DomainClient(channel)
        expected_resp = CommandResponse()
        expected_resp.events.next_sequence = 10
        client.command_handler._stub.Handle = Mock(return_value=expected_resp)

        cmd = CommandBook()
        result = client.execute(cmd)

        assert result.events.next_sequence == 10

    def test_close_closes_channel(self) -> None:
        """close closes the underlying channel."""
        channel = self._mock_channel()
        client = DomainClient(channel)
        client.close()
        channel.close.assert_called_once()


class TestCorrelationIdMetadataPropagation:
    """Audit #69 stage (b): every outbound RPC method attaches
    ``x-correlation-id`` metadata sourced from the request's canonical
    correlation field (``Query.correlation_id``,
    ``CommandPage.correlation_id``), in its UUID text form.

    Send-only — receivers don't read it. The header is for OTel
    exporters / mesh sidecars / external observability filtering.
    """

    HEADER = "x-correlation-id"

    @staticmethod
    def _channel() -> Mock:
        return Mock(spec=grpc.Channel)

    @staticmethod
    def _captured_metadata(mock_call: Mock) -> list[tuple[str, str]]:
        """Pull the ``metadata`` kwarg from the most recent call."""
        return mock_call.call_args.kwargs["metadata"]

    def test_query_client_get_event_book_attaches_metadata(self) -> None:
        client = QueryClient(self._channel())
        client._stub.GetEventBook = Mock(return_value=EventBook())

        query = Query(correlation_id=uuid_to_proto(UUID(TRACE)))
        client.get_event_book(query)
        assert (self.HEADER, TRACE) in self._captured_metadata(
            client._stub.GetEventBook
        )

    def test_query_client_get_events_attaches_metadata(self) -> None:
        client = QueryClient(self._channel())
        client._stub.GetEvents = Mock(return_value=iter([]))

        query = Query(correlation_id=uuid_to_proto(UUID(TRACE)))
        client.get_events(query)
        assert (self.HEADER, TRACE) in self._captured_metadata(client._stub.GetEvents)

    def test_command_handler_handle_attaches_metadata(self) -> None:
        client = CommandHandlerClient(self._channel())
        client._stub.Handle = Mock(return_value=CommandResponse())

        cmd = CommandBook(page=CommandPage(correlation_id=uuid_to_proto(UUID(TRACE))))
        client.handle(cmd)
        assert (self.HEADER, TRACE) in self._captured_metadata(client._stub.Handle)

    def test_empty_correlation_id_skips_header(self) -> None:
        # The header is omitted entirely (not sent as a blank value)
        # when correlation_id is empty / unset on the canonical field.
        client = QueryClient(self._channel())
        client._stub.GetEventBook = Mock(return_value=EventBook())
        client.get_event_book(Query())  # default-constructed, no cover
        md = self._captured_metadata(client._stub.GetEventBook)
        assert all(k != self.HEADER for k, _ in md)


class TestTopLevelTransportExports:
    """Audit #79: TransportMode + resolve_ch_endpoint reachable from
    the crate root, matching Rust's `lib.rs` `pub use transport::{...}`."""

    def test_transport_mode_reachable_at_top_level(self) -> None:
        import angzarr_client
        from angzarr_client.client import TransportMode as ClientTransportMode

        assert angzarr_client.TransportMode is ClientTransportMode

    def test_resolve_ch_endpoint_reachable_at_top_level(self) -> None:
        import angzarr_client
        from angzarr_client.client import resolve_ch_endpoint as client_resolve

        assert angzarr_client.resolve_ch_endpoint is client_resolve

    def test_listed_in_dunder_all(self) -> None:
        import angzarr_client

        assert "TransportMode" in angzarr_client.__all__
        assert "resolve_ch_endpoint" in angzarr_client.__all__


class TestTopLevelBindAddressExports:
    """Audit #77: ANGZARR_BIND_ADDRESS helpers reachable from crate
    root, matching Rust's `lib.rs` re-export of `resolve_bind_address`
    + `ENV_BIND_ADDRESS` + `DEFAULT_BIND_HOST`."""

    def test_resolve_bind_address_reachable(self) -> None:
        import angzarr_client
        from angzarr_client.server import resolve_bind_address as srv_fn

        assert angzarr_client.resolve_bind_address is srv_fn

    def test_constants_reachable(self) -> None:
        import angzarr_client

        assert angzarr_client.ENV_BIND_ADDRESS == "ANGZARR_BIND_ADDRESS"
        assert angzarr_client.DEFAULT_BIND_HOST == "[::]"

    def test_listed_in_dunder_all(self) -> None:
        import angzarr_client

        assert "resolve_bind_address" in angzarr_client.__all__
        assert "ENV_BIND_ADDRESS" in angzarr_client.__all__
        assert "DEFAULT_BIND_HOST" in angzarr_client.__all__
