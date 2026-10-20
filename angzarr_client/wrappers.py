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

from ._pb import (
    CommandBook as _CommandBookProto,
)
from ._pb import (
    CommandPage as _CommandPageProto,
)
from ._pb import (
    CommandResponse as _CommandResponseProto,
)
from ._pb import (
    Cover as _CoverProto,
)
from ._pb import (
    EventBook as _EventBookProto,
)
from ._pb import (
    EventPage as _EventPageProto,
)
from ._pb import (
    Query as _QueryProto,
)
from .helpers import type_url_matches

T = TypeVar("T")

# Canonical domain identifiers — user code reaches for these via
# ``from angzarr_client import UNKNOWN_DOMAIN`` etc.
UNKNOWN_DOMAIN = "unknown"


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
        if c is None or not c.HasField("edition") or not c.edition:
            return None
        return c.edition

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

    def first_command(self) -> CommandPage | None:
        """The book's command page, or None when it has none."""
        if not self._proto.HasField("page"):
            return None
        return CommandPage(self._proto.page)


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
        """The page's sequence in its stream (0 when unset)."""
        return self._proto.sequence

    def type_url(self) -> str | None:
        """Event payload's type URL, or None if missing."""
        if not self._proto.HasField("payload"):
            return None
        return self._proto.payload.type_url

    def payload(self) -> bytes | None:
        """Raw event payload bytes, or None if missing."""
        if not self._proto.HasField("payload"):
            return None
        return self._proto.payload.value

    def decode_typed(self, msg_class: type[T]) -> T | None:
        """Decode the event payload into ``msg_class`` when the type URL names
        it (the full name after the last ``/``, any prefix).

        Returns None on missing event, type mismatch, or decode failure.
        """
        if not self._proto.HasField("payload"):
            return None
        if not type_url_matches(
            self._proto.payload.type_url, msg_class.DESCRIPTOR.full_name
        ):
            return None
        try:
            msg = msg_class()
            self._proto.payload.Unpack(msg)
            return msg
        except Exception:  # noqa: BLE001 — undecodable payload decodes to None
            return None


class CommandPage(Wrapped):
    """Wrapper for the ``CommandPage`` proto."""

    def __init__(self, proto: _CommandPageProto) -> None:
        self._proto = proto

    def proto(self) -> _CommandPageProto:
        return self._proto

    def _body_payload(self):
        """The payload of the body arm (Command or Fact), or None."""
        arm = self._proto.WhichOneof("body")
        if arm is None:
            return None
        body = getattr(self._proto, arm)
        return body.payload if body.HasField("payload") else None

    def type_url(self) -> str | None:
        """The body payload's type URL (Command or Fact arm), or None if
        missing."""
        payload = self._body_payload()
        return None if payload is None else payload.type_url

    def payload(self) -> bytes | None:
        """The body's raw payload bytes (Command or Fact arm), or None if
        missing."""
        payload = self._body_payload()
        return None if payload is None else payload.value


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
