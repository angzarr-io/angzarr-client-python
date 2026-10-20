"""Tests for builder classes."""

from unittest.mock import Mock
from uuid import UUID as PyUUID
from uuid import uuid4

import pytest
from google.protobuf.wrappers_pb2 import StringValue

from angzarr_client._pb import (
    CommandResponse,
    EventBook,
)
from angzarr_client.builder import (
    CommandBuilder,
    QueryBuilder,
)
from angzarr_client.errors import InvalidArgumentError, InvalidTimestampError
from angzarr_client.helpers import proto_to_uuid

CORR = PyUUID("0192f0c1-0000-7000-8000-00000000c0de")


class TestCommandBuilder:
    """Tests for CommandBuilder."""

    def _mock_aggregate_client(self) -> Mock:
        """Create a mock CommandHandlerClient."""
        client = Mock()
        client.handle = Mock(return_value=CommandResponse())
        return client

    def test_build_minimal(self) -> None:
        """Build with minimal required fields."""
        client = self._mock_aggregate_client()
        root = PyUUID("12345678-1234-5678-1234-567812345678")
        msg = StringValue(value="test")

        builder = CommandBuilder(client, "orders", root)
        builder.with_command("/test.CreateOrder", msg)
        book = builder.build()

        assert book.cover.domain == "orders"
        assert proto_to_uuid(book.cover.root) == root
        assert book.page.WhichOneof("body") == "command"
        assert book.page.command.payload.type_url == "/test.CreateOrder"
        # No Handling: the coordinator stamps the expected sequence.
        assert not book.HasField("handling")
        # No correlation ID unless the sender sets one: the coordinator
        # assigns it.
        assert not book.page.HasField("correlation_id")

    def test_command_new_generates_uuid_v4_root(self) -> None:
        """``command_new`` materializes a client-side UUID v4 root.

        Audit #67: this is the only path to skip ``root`` — the
        CommandBuilder constructor itself requires it. Mirrors Rust
        ``CommandBuilderExt::command_new`` which also calls
        ``Uuid::new_v4()`` and passes it explicitly.
        """
        client = self._mock_aggregate_client()
        msg = StringValue(value="test")

        builder = CommandBuilder(client, "orders", uuid4())
        builder.with_command("/test.CreateOrder", msg)
        book = builder.build()

        assert book.cover.domain == "orders"
        # Client-side UUIDs for new aggregates; cover.root is always populated.
        assert book.cover.HasField("root")
        assert len(book.cover.root.value) == 16

    def test_command_builder_constructor_requires_root(self) -> None:
        """Audit #67: ``root`` is a required positional parameter; no
        default, no ``None`` accepted. Direct constructor call without
        it must raise ``TypeError`` at construction time."""
        client = self._mock_aggregate_client()
        with pytest.raises(TypeError):
            CommandBuilder(client, "orders")  # type: ignore[call-arg]

    def test_command_new_each_call_yields_independent_root(self) -> None:
        """``command_new`` called twice produces distinct UUIDs.

        Mirrors Rust's ``test_command_new_each_call_yields_independent_root``
        in builder.rs — each call gets a fresh random UUID.
        """
        client = self._mock_aggregate_client()
        msg = StringValue(value="test")

        a = CommandBuilder(client, "orders", uuid4()).with_command("type/Cmd", msg)
        b = CommandBuilder(client, "orders", uuid4()).with_command("type/Cmd", msg)
        assert a.build().cover.root.value != b.build().cover.root.value

    def test_with_correlation_id_uuid(self) -> None:
        """An explicit correlation ID is the page's correlation_id."""
        client = self._mock_aggregate_client()
        book = (
            CommandBuilder(client, "orders", uuid4())
            .with_correlation_id(CORR)
            .with_command("type/Cmd", StringValue(value="test"))
            .build()
        )

        assert proto_to_uuid(book.page.correlation_id) == CORR

    def test_with_correlation_id_accepts_uuid_text(self) -> None:
        client = self._mock_aggregate_client()
        book = (
            CommandBuilder(client, "orders", uuid4())
            .with_correlation_id(str(CORR))
            .with_command("type/Cmd", StringValue(value="test"))
            .build()
        )

        assert proto_to_uuid(book.page.correlation_id) == CORR

    def test_with_correlation_id_refuses_non_uuid_text(self) -> None:
        """Correlation IDs are UUIDs on the wire (CommandPage.correlation_id)."""
        builder = CommandBuilder(self._mock_aggregate_client(), "orders", uuid4())

        with pytest.raises(InvalidArgumentError) as exc_info:
            builder.with_correlation_id("my-corr-123")
        assert exc_info.value.code == "PROTO_UUID_INVALID"
        assert exc_info.value.details == {
            "field": "correlation_id",
            "input": "my-corr-123",
        }

    def test_build_without_type_url_raises(self) -> None:
        """Build without type_url raises InvalidArgumentError."""
        client = self._mock_aggregate_client()
        builder = CommandBuilder(client, "orders", uuid4())

        with pytest.raises(InvalidArgumentError) as exc_info:
            builder.build()
        assert "type_url" in str(exc_info.value)
        assert exc_info.value.code == "COMMAND_TYPE_URL_MISSING"

    def test_build_without_payload_raises(self) -> None:
        """Build with type_url but no payload raises."""
        client = self._mock_aggregate_client()
        builder = CommandBuilder(client, "orders", uuid4())
        builder.with_type_url("/test.Cmd")

        with pytest.raises(InvalidArgumentError) as exc_info:
            builder.build()
        assert "payload" in str(exc_info.value)
        assert exc_info.value.code == "COMMAND_PAYLOAD_MISSING"

    def test_build_with_type_url_and_payload(self) -> None:
        """with_type_url + with_payload carry the pre-encoded command."""
        client = self._mock_aggregate_client()
        payload = StringValue(value="raw").SerializeToString()
        book = (
            CommandBuilder(client, "orders", uuid4())
            .with_type_url("/google.protobuf.StringValue")
            .with_payload(payload)
            .build()
        )

        assert book.page.command.payload.type_url == "/google.protobuf.StringValue"
        assert book.page.command.payload.value == payload

    def test_execute_sends_the_built_book_through_handle(self) -> None:
        """Execute builds and calls client.handle (DECISION)."""
        client = self._mock_aggregate_client()
        expected_response = CommandResponse()
        expected_response.events.next_sequence = 5
        client.handle.return_value = expected_response

        response = (
            CommandBuilder(client, "orders", uuid4())
            .with_correlation_id(CORR)
            .with_command("type/Cmd", StringValue(value="test"))
            .execute()
        )

        client.handle.assert_called_once()
        (sent,) = client.handle.call_args.args
        assert proto_to_uuid(sent.page.correlation_id) == CORR
        assert response.events.next_sequence == 5

    def test_fluent_chaining(self) -> None:
        """Methods return self for chaining."""
        client = self._mock_aggregate_client()
        msg = StringValue(value="test")
        root = PyUUID("12345678-1234-5678-1234-567812345678")

        result = (
            CommandBuilder(client, "orders", root)
            .with_correlation_id(CORR)
            .with_command("type/Cmd", msg)
        )

        assert isinstance(result, CommandBuilder)


class TestQueryBuilder:
    """Tests for QueryBuilder."""

    def _mock_query_client(self) -> Mock:
        """Create a mock QueryClient."""
        client = Mock()
        book = EventBook()
        book.next_sequence = 10
        client.get_event_book = Mock(return_value=book)
        client.get_events = Mock(return_value=[book])
        return client

    def test_build_with_root(self) -> None:
        """Build query for specific aggregate."""
        client = self._mock_query_client()
        root = PyUUID("12345678-1234-5678-1234-567812345678")

        builder = QueryBuilder(client, "orders", root)
        query = builder.build()

        assert query.cover.domain == "orders"
        assert proto_to_uuid(query.cover.root) == root

    def test_build_by_correlation_id(self) -> None:
        """Build query by correlation ID."""
        client = self._mock_query_client()

        builder = QueryBuilder(client, "orders")
        builder.by_correlation_id(CORR)
        query = builder.build()

        assert proto_to_uuid(query.correlation_id) == CORR
        assert not query.cover.HasField("root")

    def test_by_correlation_id_clears_root(self) -> None:
        """by_correlation_id clears the root."""
        client = self._mock_query_client()
        root = PyUUID("12345678-1234-5678-1234-567812345678")

        builder = QueryBuilder(client, "orders", root)
        builder.by_correlation_id(str(CORR))
        query = builder.build()

        assert proto_to_uuid(query.correlation_id) == CORR
        assert not query.cover.HasField("root")

    def test_by_correlation_id_refuses_non_uuid_text(self) -> None:
        builder = QueryBuilder(self._mock_query_client(), "orders")
        with pytest.raises(InvalidArgumentError) as exc_info:
            builder.by_correlation_id("corr-abc")
        assert exc_info.value.code == "PROTO_UUID_INVALID"

    def test_with_edition(self) -> None:
        """Build query with specific edition."""
        client = self._mock_query_client()

        builder = QueryBuilder(client, "orders")
        builder.with_edition("branch-a")
        query = builder.build()

        assert query.cover.edition == "branch-a"

    def test_range_lower_only(self) -> None:
        """Build query with lower bound range."""
        client = self._mock_query_client()

        builder = QueryBuilder(client, "orders")
        builder.range(5)
        query = builder.build()

        assert query.range.lower == 5

    def test_range_to(self) -> None:
        """Build query with both range bounds."""
        client = self._mock_query_client()

        builder = QueryBuilder(client, "orders")
        builder.range_to(5, 10)
        query = builder.build()

        assert query.range.lower == 5
        assert query.range.upper == 10

    def test_as_of_sequence(self) -> None:
        """Build temporal query by sequence."""
        client = self._mock_query_client()

        builder = QueryBuilder(client, "orders")
        builder.as_of_sequence(42)
        query = builder.build()

        assert query.temporal.as_of_sequence == 42

    def test_as_of_time_valid(self) -> None:
        """Build temporal query by time."""
        client = self._mock_query_client()

        builder = QueryBuilder(client, "orders")
        builder.as_of_time("2024-01-15T10:30:00Z")
        query = builder.build()

        assert query.temporal.as_of_time.seconds > 0

    def test_as_of_time_invalid_raises_immediately(self) -> None:
        """Audit finding #34 (Option B): a malformed RFC3339 string raises
        ``InvalidTimestampError`` synchronously at the call site, not
        deferred to ``build()``. The previous deferred-error pattern
        survived last-call-wins setters and produced stale-error bugs."""
        client = self._mock_query_client()

        builder = QueryBuilder(client, "orders")
        with pytest.raises(InvalidTimestampError):
            builder.as_of_time("not-a-timestamp")

    def test_as_of_time_failure_does_not_pollute_subsequent_setters(self) -> None:
        """After a failed `as_of_time(...)`, subsequent last-call-wins
        setters work normally — no sticky `_err` survives."""
        client = self._mock_query_client()

        builder = QueryBuilder(client, "orders")
        with pytest.raises(InvalidTimestampError):
            builder.as_of_time("not-a-timestamp")

        # Same builder instance: a valid setter call now succeeds and
        # build() must NOT raise the stale parse error.
        builder.as_of_sequence(5)
        query = builder.build()
        assert query.temporal.as_of_sequence == 5

    def test_get_event_book(self) -> None:
        """get_event_book executes query."""
        client = self._mock_query_client()
        expected_book = EventBook()
        expected_book.next_sequence = 42
        client.get_event_book.return_value = expected_book

        builder = QueryBuilder(client, "orders")
        result = builder.get_event_book()

        client.get_event_book.assert_called_once()
        assert result.next_sequence == 42

    def test_get_events(self) -> None:
        """get_events returns list of books."""
        client = self._mock_query_client()
        book1 = EventBook()
        book1.next_sequence = 5
        book2 = EventBook()
        book2.next_sequence = 10
        client.get_events.return_value = [book1, book2]

        builder = QueryBuilder(client, "orders")
        result = builder.get_events()

        assert len(result) == 2
        assert result[0].next_sequence == 5
        assert result[1].next_sequence == 10

    def test_get_pages(self) -> None:
        """get_pages extracts pages from book."""
        client = self._mock_query_client()
        book = EventBook()
        book.pages.add().sequence = 1
        book.pages.add().sequence = 2
        client.get_event_book.return_value = book

        builder = QueryBuilder(client, "orders")
        result = builder.get_pages()

        assert len(result) == 2
        assert result[0].sequence == 1
        assert result[1].sequence == 2

    def test_fluent_chaining(self) -> None:
        """Methods return self for chaining."""
        client = self._mock_query_client()
        root = PyUUID("12345678-1234-5678-1234-567812345678")

        result = QueryBuilder(client, "orders", root).with_edition("v2").range_to(0, 10)

        assert isinstance(result, QueryBuilder)
