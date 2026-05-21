"""Fluent builders for commands and queries."""

from uuid import UUID as PyUUID
from uuid import uuid4

from google.protobuf.any_pb2 import Any as ProtoAny
from google.protobuf.message import Message

from .client import CommandHandlerClient, QueryClient
from .errors import InvalidArgumentError
from .helpers import implicit_edition, parse_timestamp, uuid_to_proto
from .proto.angzarr import (
    CommandBook,
    CommandPage,
    CommandRequest,
    CommandResponse,
    Cover,
    EventBook,
    EventPage,
    MergeStrategy,
    PageHeader,
    Query,
    SequenceRange,
    SyncMode,
    TemporalQuery,
)


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
        self._correlation_id: str | None = None
        self._sequence: int = 0
        self._sequence_set: bool = False
        self._merge_strategy: MergeStrategy = MergeStrategy.MERGE_COMMUTATIVE
        self._sync_mode: SyncMode | None = None
        self._type_url: str | None = None
        self._payload: bytes | None = None

    def with_correlation_id(self, id: str) -> "CommandBuilder":
        """Set the correlation ID for request tracing."""
        self._correlation_id = id
        return self

    def with_sequence(self, seq: int) -> "CommandBuilder":
        """Set the expected sequence number for optimistic locking."""
        self._sequence = seq
        self._sequence_set = True
        return self

    def with_merge_strategy(self, strategy: MergeStrategy) -> "CommandBuilder":
        """Set the merge strategy for conflict resolution.

        Args:
            strategy: MERGE_COMMUTATIVE (default) or MERGE_STRICT.
        """
        self._merge_strategy = strategy
        return self

    def with_sync_mode(self, mode: SyncMode) -> "CommandBuilder":
        """Stamp the sync mode onto the built ``CommandPage``'s header.

        When unset, :meth:`build` emits a header with ``sync_mode``
        cleared, and :meth:`execute` supplies ``SYNC_MODE_ASYNC`` (the
        cross-language default). Calling this makes the ``build()``
        output round-trip the choice — important for callers who hand
        the produced :class:`CommandBook` to a transport helper that
        doesn't accept a separate ``sync_mode`` argument.

        Mirrors Rust's ``CommandBuilder::with_sync_mode``
        (``builder.rs:80-83``).
        """
        self._sync_mode = mode
        return self

    def with_command(self, type_url: str, message: Message) -> "CommandBuilder":
        """Set the command type URL and message."""
        self._type_url = type_url
        self._payload = message.SerializeToString()
        return self

    def build(self) -> CommandBook:
        """Build the CommandBook without executing."""
        if not self._type_url:
            raise InvalidArgumentError("command type_url not set")
        if self._payload is None:
            raise InvalidArgumentError("command payload not set")
        if not self._sequence_set:
            raise InvalidArgumentError("sequence not set (call with_sequence)")

        correlation_id = self._correlation_id or str(uuid4())

        cover = Cover(
            domain=self._domain,
            correlation_id=correlation_id,
        )
        cover.root.CopyFrom(uuid_to_proto(self._root))

        command_any = ProtoAny(type_url=self._type_url, value=self._payload)
        header = PageHeader(sequence=self._sequence)
        if self._sync_mode is not None:
            header.sync_mode = self._sync_mode
        page = CommandPage(header=header, merge_strategy=self._merge_strategy)
        page.command.CopyFrom(command_any)

        book = CommandBook()
        book.cover.CopyFrom(cover)
        book.pages.append(page)
        return book

    def execute(self, sync_mode: SyncMode | None = None) -> CommandResponse:
        """Build and execute the command.

        Args:
            sync_mode: Execution mode (ASYNC, SIMPLE, CASCADE, …).
                When ``None`` (the default), falls back to the mode
                stored by :meth:`with_sync_mode`; if neither was set,
                ``SYNC_MODE_ASYNC`` is used. Mirrors Rust's
                ``CommandBuilder::execute`` (``builder.rs:155-160``).
        """
        if sync_mode is None:
            sync_mode = (
                self._sync_mode
                if self._sync_mode is not None
                else SyncMode.SYNC_MODE_ASYNC
            )
        # Round-trip the chosen mode through the built header too, so the
        # serialized CommandBook reflects the same decision the request
        # envelope carries — keeps build() and execute() coherent for
        # callers who inspect the produced book.
        prev_mode = self._sync_mode
        self._sync_mode = sync_mode
        try:
            cmd = self.build()
        finally:
            self._sync_mode = prev_mode
        request = CommandRequest(command=cmd, sync_mode=sync_mode)
        return self._client.handle_command(request)


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
        self._correlation_id: str | None = None
        # Single selection slot — matches Rust's `selection: Option<Selection>`
        # so chained range/as_of_sequence/as_of_time setters last-wins
        # instead of leaving stale state behind. P2.2-style "single slot,
        # overwrite on each call" — see PARITY_AUDIT.md finding #23.
        self._range: SequenceRange | None = None
        self._temporal: TemporalQuery | None = None
        self._edition: str | None = None

    def by_correlation_id(self, id: str) -> "QueryBuilder":
        """Query by correlation ID instead of root."""
        self._correlation_id = id
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

        cover = Cover(
            domain=self._domain,
            correlation_id=self._correlation_id or "",
        )
        if self._root:
            cover.root.CopyFrom(uuid_to_proto(self._root))
        if self._edition:
            cover.edition.CopyFrom(implicit_edition(self._edition))

        query = Query()
        query.cover.CopyFrom(cover)

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
