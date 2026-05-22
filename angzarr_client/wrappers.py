"""User-facing wrappers around Angzarr framework proto types.

Each wrapper takes the underlying proto in its constructor and exposes
domain accessors as methods. The raw proto is reachable via the
:meth:`Wrapped.proto` interface method (``wrapper.proto()``) when
callers need direct field access.

Cover-bearing wrappers (:class:`Cover`, :class:`EventBook`,
:class:`CommandBook`, :class:`Query`) inherit shared accessors from
:class:`CoverBearer`. Page wrappers (:class:`EventPage`,
:class:`CommandPage`) and :class:`CommandResponse` are not Cover-bearers
and define their own surface.

Naming: wrapper class names shadow the proto type names. Internal code
that wants the raw proto imports it from
``angzarr_client.proto.angzarr`` directly; user code reaches for the
wrapper from ``angzarr_client``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TypeVar
from uuid import UUID as PyUUID

from .proto.angzarr.v1.command_handler_pb2 import (
    CommandResponse as _CommandResponseProto,
)
from .proto.angzarr.v1.types_pb2 import (
    CommandBook as _CommandBookProto,
    CommandPage as _CommandPageProto,
    Cover as _CoverProto,
    EventBook as _EventBookProto,
    EventPage as _EventPageProto,
    MergeStrategy,
    PageHeader,
    Query as _QueryProto,
)

T = TypeVar("T")

# Canonical domain identifiers — user code reaches for these via
# ``from angzarr_client import UNKNOWN_DOMAIN`` etc.
UNKNOWN_DOMAIN = "unknown"
TYPE_URL_PREFIX = "type.googleapis.com/"


class Wrapped(ABC):
    """Interface every angzarr wrapper implements.

    Wrappers expose two surfaces:

    1. Method-style accessors for common needs (e.g. :meth:`Cover.domain`).
    2. The raw proto for everything else, via :meth:`proto`.

    Cross-language note: ``proto()`` is the documented escape hatch for
    callers that want direct proto field access (e.g. for serialization,
    for fields that don't have a wrapper accessor, for proto-specific
    methods like ``HasField``). Other languages will provide an
    equivalent method (e.g. Java ``Cover.proto()``).
    """

    @abstractmethod
    def proto(self):
        """Return the wrapped proto message."""


class CoverBearer(Wrapped):
    """Shared accessors for proto types that carry a ``Cover`` field.

    Subclasses set ``self._proto`` and override :meth:`_cover_proto` to
    return the embedded Cover (or ``None`` if missing). The default
    implementation assumes ``self._proto`` is itself a Cover — only
    :class:`Cover` relies on the default; the others override.
    """

    def _cover_proto(self) -> _CoverProto | None:
        # Default: the wrapped proto IS a Cover (used by Cover wrapper).
        return self.proto()  # type: ignore[return-value]

    def domain(self) -> str:
        """Get the domain, falling back to :data:`UNKNOWN_DOMAIN`."""
        c = self._cover_proto()
        if c is None or not c.domain:
            return UNKNOWN_DOMAIN
        return c.domain

    def correlation_id(self) -> str:
        """Get the correlation_id, or empty string if missing."""
        c = self._cover_proto()
        if c is None:
            return ""
        return c.correlation_id

    def has_correlation_id(self) -> bool:
        """True if a non-empty correlation_id is present."""
        return bool(self.correlation_id())

    def root_uuid(self) -> PyUUID | None:
        """Extract the root UUID, or None if missing/malformed."""
        c = self._cover_proto()
        if c is None or not c.HasField("root"):
            return None
        try:
            return PyUUID(bytes=c.root.value)
        except ValueError:
            return None

    def root_id_hex(self) -> str:
        """Root UUID as hex, or empty string if missing."""
        c = self._cover_proto()
        if c is None or not c.HasField("root"):
            return ""
        return c.root.value.hex()

    def edition(self) -> str | None:
        """Edition name, or None when missing/empty."""
        c = self._cover_proto()
        if c is None or not c.HasField("edition") or not c.edition.name:
            return None
        return c.edition.name

    def routing_key(self) -> str:
        """Bus routing key (currently the domain)."""
        return self.domain()

    def cache_key(self) -> str:
        """Cache key derived from edition + domain + root."""
        return f"{self.edition() or ''}:{self.domain()}:{self.root_id_hex()}"


class Cover(CoverBearer):
    """Wrapper for the ``Cover`` proto."""

    def __init__(self, proto: _CoverProto) -> None:
        self._proto = proto

    def proto(self) -> _CoverProto:
        return self._proto


class EventBook(CoverBearer):
    """Wrapper for the ``EventBook`` proto."""

    def __init__(self, proto: _EventBookProto) -> None:
        self._proto = proto

    def proto(self) -> _EventBookProto:
        return self._proto

    def _cover_proto(self) -> _CoverProto | None:
        return self._proto.cover if self._proto.HasField("cover") else None

    def cover(self) -> Cover:
        """The wrapped cover.

        Always returns a :class:`Cover` — when the underlying proto
        has no cover field set, the wrapper is built around the
        proto's default-instance cover, so accessors like
        ``.domain()`` still work (returning the canonical empty
        responses, e.g. :data:`UNKNOWN_DOMAIN`).
        """
        return Cover(self._proto.cover)

    def next_sequence(self) -> int:
        """Framework-precomputed next sequence number."""
        return self._proto.next_sequence

    def is_empty(self) -> bool:
        """True if there are no event pages."""
        return len(self._proto.pages) == 0

    def pages(self) -> list[EventPage]:
        """All event pages, wrapped."""
        return [EventPage(p) for p in self._proto.pages]

    def first_page(self) -> EventPage | None:
        """First event page, or None when empty."""
        if not self._proto.pages:
            return None
        return EventPage(self._proto.pages[0])

    def last_page(self) -> EventPage | None:
        """Last event page, or None when empty."""
        if not self._proto.pages:
            return None
        return EventPage(self._proto.pages[-1])


class CommandBook(CoverBearer):
    """Wrapper for the ``CommandBook`` proto."""

    def __init__(self, proto: _CommandBookProto) -> None:
        self._proto = proto

    def proto(self) -> _CommandBookProto:
        return self._proto

    def _cover_proto(self) -> _CoverProto | None:
        return self._proto.cover if self._proto.HasField("cover") else None

    def cover(self) -> Cover:
        """The wrapped cover (always present; default-instance if not set).

        See :meth:`EventBook.cover` for the rationale.
        """
        return Cover(self._proto.cover)

    def pages(self) -> list[CommandPage]:
        """All command pages, wrapped."""
        return [CommandPage(p) for p in self._proto.pages]

    def first_command(self) -> CommandPage | None:
        """First command page, or None when empty."""
        if not self._proto.pages:
            return None
        return CommandPage(self._proto.pages[0])

    def command_sequence(self) -> int:
        """Sequence number of the first command page (0 when empty)."""
        if not self._proto.pages:
            return 0
        page = self._proto.pages[0]
        if not page.HasField("header"):
            return 0
        return page.header.sequence

    def merge_strategy(self) -> MergeStrategy:
        """Merge strategy of the first page; defaults to commutative."""
        if not self._proto.pages:
            return MergeStrategy.MERGE_COMMUTATIVE
        return self._proto.pages[0].merge_strategy


class Query(CoverBearer):
    """Wrapper for the ``Query`` proto."""

    def __init__(self, proto: _QueryProto) -> None:
        self._proto = proto

    def proto(self) -> _QueryProto:
        return self._proto

    def _cover_proto(self) -> _CoverProto | None:
        return self._proto.cover if self._proto.HasField("cover") else None

    def cover(self) -> Cover:
        """The wrapped cover (always present; default-instance if not set).

        See :meth:`EventBook.cover` for the rationale.
        """
        return Cover(self._proto.cover)


class EventPage(Wrapped):
    """Wrapper for the ``EventPage`` proto."""

    def __init__(self, proto: _EventPageProto) -> None:
        self._proto = proto

    def proto(self) -> _EventPageProto:
        return self._proto

    def sequence_num(self) -> int:
        """Explicit sequence number, or 0 if not set."""
        if not self._proto.HasField("header"):
            return 0
        return self._proto.header.sequence

    def header(self) -> PageHeader | None:
        """The page header proto, or None if missing."""
        if not self._proto.HasField("header"):
            return None
        return self._proto.header

    def is_deferred(self) -> bool:
        """True if this page carries a deferred-sequence header."""
        if not self._proto.HasField("header"):
            return False
        h = self._proto.header
        return h.HasField("external_deferred") or h.HasField("angzarr_deferred")

    def type_url(self) -> str | None:
        """Event payload's type URL, or None if missing."""
        if not self._proto.HasField("event"):
            return None
        return self._proto.event.type_url

    def payload(self) -> bytes | None:
        """Raw event payload bytes, or None if missing."""
        if not self._proto.HasField("event"):
            return None
        return self._proto.event.value

    def decode_typed(self, msg_class: type[T]) -> T | None:
        """Decode the event payload into ``msg_class``, exact-match on type URL.

        Returns None on missing event, type mismatch, or decode failure.
        """
        if not self._proto.HasField("event"):
            return None
        expected = TYPE_URL_PREFIX + msg_class.DESCRIPTOR.full_name
        if self._proto.event.type_url != expected:
            return None
        try:
            msg = msg_class()
            self._proto.event.Unpack(msg)
            return msg
        except Exception:
            return None


class CommandPage(Wrapped):
    """Wrapper for the ``CommandPage`` proto."""

    def __init__(self, proto: _CommandPageProto) -> None:
        self._proto = proto

    def proto(self) -> _CommandPageProto:
        return self._proto

    def sequence_num(self) -> int:
        """Explicit sequence number, or 0 if not set."""
        if not self._proto.HasField("header"):
            return 0
        return self._proto.header.sequence

    def header(self) -> PageHeader | None:
        """The page header proto, or None if missing."""
        if not self._proto.HasField("header"):
            return None
        return self._proto.header

    def is_deferred(self) -> bool:
        """True if this page carries a deferred-sequence header."""
        if not self._proto.HasField("header"):
            return False
        h = self._proto.header
        return h.HasField("external_deferred") or h.HasField("angzarr_deferred")

    def type_url(self) -> str | None:
        """Command payload's type URL, or None if missing."""
        if not self._proto.HasField("command"):
            return None
        return self._proto.command.type_url

    def payload(self) -> bytes | None:
        """Raw command payload bytes, or None if missing."""
        if not self._proto.HasField("command"):
            return None
        return self._proto.command.value

    def merge_strategy(self) -> MergeStrategy:
        """Per-page merge strategy."""
        return self._proto.merge_strategy


class CommandResponse(Wrapped):
    """Wrapper for the ``CommandResponse`` proto."""

    def __init__(self, proto: _CommandResponseProto) -> None:
        self._proto = proto

    def proto(self) -> _CommandResponseProto:
        return self._proto

    def events_book(self) -> EventBook | None:
        """The wrapped events EventBook, or None if not set."""
        if not self._proto.HasField("events"):
            return None
        return EventBook(self._proto.events)

    def events(self) -> list[EventPage]:
        """All event pages from the response, wrapped."""
        if not self._proto.HasField("events"):
            return []
        return [EventPage(p) for p in self._proto.events.pages]
