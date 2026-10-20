"""Fluent builders for commands and queries."""

from uuid import UUID as PyUUID
from uuid import uuid4

from google.protobuf.any_pb2 import Any as ProtoAny
from google.protobuf.message import Message

from ._pb import (
    Command,
    CommandBook,
    CommandPage,
    CommandResponse,
    ConflictResolution,
    Cover,
    EventBook,
    EventPage,
    Query,
    SequenceRange,
    TemporalQuery,
)
from .client import CommandHandlerClient, QueryClient
from .error_codes import codes, keys, messages
from .errors import InvalidArgumentError
from .helpers import parse_timestamp, uuid_to_proto


def _as_uuid(value: PyUUID | str, field: str) -> PyUUID:
    """``value`` as a UUID: a UUID as given, or a UUID's text form parsed.
    Anything else is refused with ``PROTO_UUID_INVALID``."""
    if isinstance(value, PyUUID):
        return value
    try:
        return PyUUID(value)
    except (TypeError, ValueError) as exc:
        raise InvalidArgumentError(
            messages.PROTO_UUID_INVALID,
            code=codes.PROTO_UUID_INVALID,
            details={keys.FIELD: field, keys.INPUT: value},
        ) from exc


class CommandBuilder:
    """Fluent builder for constructing and executing commands.

    Audit #67: ``root`` is required. The only path to an
    auto-generated UUID v4 is via :func:`command_new`, which
    materializes the UUID and passes it explicitly to the constructor.
    Aggregate roots are always client-assigned across all six languages
    (audit #20 convention).
    """

    def __init__(
        self,
        client: CommandHandlerClient,
        domain: str,
        root: PyUUID,
    ):
        self._client = client
        self._domain = domain
        self._root = root
        self._correlation_id: PyUUID | None = None
        self._type_url: str | None = None
        self._payload: bytes | None = None
        self._expected_sequence: int | None = None
        self._conflict_resolution = ConflictResolution.CONFLICT_RESOLUTION_UNSPECIFIED

    def with_correlation_id(self, id: PyUUID | str) -> "CommandBuilder":
        """Set the correlation ID for request tracing
        (``CommandPage.correlation_id``): a UUID, or a UUID's text form
        (anything else raises :class:`InvalidArgumentError`)."""
        self._correlation_id = _as_uuid(id, "correlation_id")
        return self

    def with_command(self, type_url: str, message: Message) -> "CommandBuilder":
        """Set the command type URL and message."""
        self._type_url = type_url
        self._payload = message.SerializeToString()
        return self

    def with_type_url(self, type_url: str) -> "CommandBuilder":
        """Set the command type URL on its own; pair with :meth:`with_payload`."""
        self._type_url = type_url
        return self

    def with_payload(self, payload: bytes) -> "CommandBuilder":
        """Set the already-encoded command payload; pair with :meth:`with_type_url`."""
        self._payload = payload
        return self

    def with_expected_sequence(self, sequence: int) -> "CommandBuilder":
        """Set ``Handling.expected_sequence``: the next sequence of the
        target stream the command was issued against (0 for a new stream,
        sent as set). Left unset, nothing is sent and the coordinator stamps
        the head it decides against."""
        self._expected_sequence = sequence
        return self

    def with_conflict_resolution(self, resolution: int) -> "CommandBuilder":
        """Set ``Handling.conflict_resolution`` (a :class:`ConflictResolution`
        value): what to do with a stale expected sequence. Left unset, the
        configured default applies."""
        self._conflict_resolution = resolution
        return self

    def build(self) -> CommandBook:
        """Build the CommandBook without executing.

        Requires a type URL and payload (:meth:`with_command`, or
        :meth:`with_type_url` plus :meth:`with_payload`); otherwise raises
        :class:`InvalidArgumentError` with code ``COMMAND_TYPE_URL_MISSING``
        / ``COMMAND_PAYLOAD_MISSING``. The correlation ID defaults to a
        fresh UUID v4.
        """
        if not self._type_url:
            raise InvalidArgumentError(
                messages.COMMAND_TYPE_URL_MISSING,
                code=codes.COMMAND_TYPE_URL_MISSING,
            )
        if self._payload is None:
            raise InvalidArgumentError(
                messages.COMMAND_PAYLOAD_MISSING,
                code=codes.COMMAND_PAYLOAD_MISSING,
            )

        correlation_id = self._correlation_id or uuid4()

        cover = Cover(domain=self._domain)
        cover.root.CopyFrom(uuid_to_proto(self._root))

        page = CommandPage(
            command=Command(
                payload=ProtoAny(type_url=self._type_url, value=self._payload)
            )
        )
        page.correlation_id.CopyFrom(uuid_to_proto(correlation_id))

        book = CommandBook()
        book.cover.CopyFrom(cover)
        book.page.CopyFrom(page)
        if self._expected_sequence is not None:
            book.handling.expected_sequence = self._expected_sequence
        if self._conflict_resolution:
            book.handling.conflict_resolution = self._conflict_resolution
        return book

    def execute_async(self) -> None:
        """Build the command and send it without waiting for its decision
        (ASYNC; see :meth:`CommandHandlerClient.handle_async`)."""
        self._client.handle_async(self.build())

    def execute(self) -> CommandResponse:
        """Build the command and send it (DECISION: the response carries the
        persisted events)."""
        return self._client.handle(self.build())

    def execute_sync(self) -> CommandResponse:
        """Build the command and send it, waiting for the synchronous
        projectors too (SIMPLE; see :meth:`CommandHandlerClient.handle_sync`)."""
        return self._client.handle_sync(self.build())


class QueryBuilder:
    """Fluent builder for constructing and executing queries."""

    def __init__(
        self,
        client: QueryClient,
        domain: str,
        root: PyUUID | None = None,
    ):
        self._client = client
        self._domain = domain
        self._root = root
        self._correlation_id: PyUUID | None = None
        # Single selection slot — matches Rust's `selection: Option<Selection>`
        # so chained range/as_of_sequence/as_of_time setters last-wins
        # instead of leaving stale state behind. P2.2-style "single slot,
        # overwrite on each call" — see PARITY_AUDIT.md finding #23.
        self._range: SequenceRange | None = None
        self._temporal: TemporalQuery | None = None
        self._edition: str | None = None

    def by_correlation_id(self, id: PyUUID | str) -> "QueryBuilder":
        """Query by correlation ID (``Query.correlation_id``) instead of root:
        a UUID, or a UUID's text form (anything else raises
        :class:`InvalidArgumentError`)."""
        self._correlation_id = _as_uuid(id, "correlation_id")
        self._root = None
        return self

    def edition(self, edition: str) -> "QueryBuilder":
        """Query events from a specific edition."""
        self._edition = edition
        return self

    # Alias for backwards compatibility
    with_edition = edition

    def range(self, lower: int) -> "QueryBuilder":
        """Query a range of sequences from lower (inclusive).

        Last-selection-wins: clears any previously-set temporal
        selection so chained calls like `.as_of_sequence(10).range(5)`
        produce a Query with only the range. Mirrors Rust's
        `builder.rs:165` single-slot semantics (PARITY_AUDIT.md
        finding #23).
        """
        self._range = SequenceRange(lower=lower)
        self._temporal = None
        return self

    def range_to(self, lower: int, upper: int) -> "QueryBuilder":
        """Query a range of sequences with upper bound (inclusive).

        Last-selection-wins (see :meth:`range`).
        """
        self._range = SequenceRange(lower=lower, upper=upper)
        self._temporal = None
        return self

    def as_of_sequence(self, seq: int) -> "QueryBuilder":
        """Query state as of a specific sequence number.

        Last-selection-wins: clears any previously-set range selection.
        """
        self._temporal = TemporalQuery(as_of_sequence=seq)
        self._range = None
        return self

    def as_of_time(self, rfc3339: str) -> "QueryBuilder":
        """Query state as of a specific timestamp (RFC3339 format).

        Last-selection-wins: clears any previously-set range selection.

        Audit finding #34 (Option B — raise immediately): a malformed
        ``rfc3339`` string raises ``InvalidTimestampError`` synchronously
        rather than deferring to ``build()``. Previously the failure was
        captured into a sticky ``_err`` field that survived subsequent
        last-call-wins setters, making `qb.as_of_time("bad").as_of_sequence(5).build()`
        raise the stale parse error. Mirrors Rust's
        ``as_of_time(...) -> Result<Self>`` signature where the bad
        call short-circuits at the call site.
        """
        ts = parse_timestamp(rfc3339)
        self._temporal = TemporalQuery()
        self._temporal.as_of_time.CopyFrom(ts)
        self._range = None
        return self

    def build(self) -> Query:
        """Build the Query without executing."""

        cover = Cover(domain=self._domain)
        if self._root:
            cover.root.CopyFrom(uuid_to_proto(self._root))
        if self._edition:
            cover.edition = self._edition

        query = Query()
        query.cover.CopyFrom(cover)
        if self._correlation_id is not None:
            query.correlation_id.CopyFrom(uuid_to_proto(self._correlation_id))

        if self._range:
            query.range.CopyFrom(self._range)
        elif self._temporal:
            query.temporal.CopyFrom(self._temporal)

        return query

    def get_event_book(self) -> EventBook:
        """Execute the query and return a single EventBook."""
        query = self.build()
        return self._client.get_event_book(query)

    def get_events(self) -> list[EventBook]:
        """Execute the query and return all matching EventBooks."""
        query = self.build()
        return self._client.get_events(query)

    def get_pages(self) -> list[EventPage]:
        """Execute the query and return just the event pages."""
        book = self.get_event_book()
        return list(book.pages)
