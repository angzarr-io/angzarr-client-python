"""Pure utility helpers for working with Angzarr proto types.

Cover/book/page accessors live on the wrapper classes in
``angzarr_client.wrappers`` (e.g. ``Cover.domain()``,
``EventBook.next_sequence()``). What stays here are functions that
either don't take a wrapped proto, take a primitive, or build a new
proto from scratch.
"""

from datetime import datetime, timezone
from typing import TypeVar
from uuid import UUID as PyUUID

from google.protobuf.any_pb2 import Any as ProtoAny
from google.protobuf.message import Message
from google.protobuf.timestamp_pb2 import Timestamp

from .errors import InvalidTimestampError
from .proto.angzarr import (
    UUID,
    CommandBook,
    CommandPage,
    Cover,
    Edition,
    EventBook,
    EventPage,
    PageHeader,
    Query,
    SequenceRange,
    TemporalQuery,
)

# Type variable for generic message type
T = TypeVar("T", bound=Message)

# Constants matching Rust proto_ext::constants
UNKNOWN_DOMAIN = "unknown"
WILDCARD_DOMAIN = "*"
DEFAULT_EDITION = ""
META_ANGZARR_DOMAIN = "_angzarr"
PROJECTION_DOMAIN_PREFIX = "_projection"
PROJECTION_TYPE_URL = "angzarr_client.proto.angzarr.v1.Projection"
CORRELATION_ID_HEADER = "x-correlation-id"
TYPE_URL_PREFIX = "type.googleapis.com/"


# UUID conversion


def uuid_to_proto(u: PyUUID) -> UUID:
    """Convert a Python UUID to a proto UUID."""
    return UUID(value=u.bytes)


def proto_to_uuid(u: UUID) -> PyUUID:
    """Convert a proto UUID to Python UUID."""
    return PyUUID(bytes=u.value)


def proto_uuid_to_hex(u: UUID | None) -> str:
    """Convert a proto UUID to hex string format."""
    if u is None:
        return ""
    return u.value.hex()


def bytes_to_uuid_text(b: bytes) -> str:
    """Convert raw bytes to a standard UUID text format.

    16-byte input formats as the canonical 8-4-4-4-12 UUID; other lengths
    fall back to hex.
    """
    if len(b) == 16:
        return str(PyUUID(bytes=b))
    return b.hex()


def proto_uuid_to_text(u: UUID | None) -> str:
    """Convert a proto UUID to its standard text format."""
    if u is None:
        return ""
    return bytes_to_uuid_text(u.value)


# Edition helpers


def implicit_edition(name: str) -> Edition:
    """Create an edition with the given name but no divergences."""
    return Edition(name=name)


# Audit #86 reverted 2026-04-29: edition propagation moved to
# coordinator-contract; READ-side accessors live on the wrapper
# classes (e.g. ``Cover.edition()``).


def divergence_for(e: Edition | None, domain_name: str) -> int | None:
    """Return the divergence sequence for a domain, or ``None`` if not found."""
    if e is None:
        return None
    for d in e.divergences:
        if d.domain == domain_name:
            return d.sequence
    return None


# EventBook / response extraction utilities (string- or class-keyed —
# wrappers handle the typed-message-class shortcut).


def events_from_response(resp) -> list[EventPage]:
    """Extract the event pages list from a ``CommandResponse``.

    Returns ``[]`` for a missing/empty events book. The wrapper-method
    equivalent is ``CommandResponse(resp).events()`` (which returns a
    list of wrapped pages); this free function returns raw protos for
    the common case where the caller just wants to iterate them.
    """
    if resp is None or not resp.HasField("events"):
        return []
    return list(resp.events.pages)


def idempotency_key(deferred) -> str | None:
    """Build the composite idempotency key for a saga-produced deferred sequence.

    Format: ``{source.edition}:{source.domain}:{source.root_hex}:{source_seq}``.
    Returns ``None`` when the deferred sequence has no source cover —
    a malformed wire input is a missing-key signal, not an exception.
    Audit finding #55.
    """
    if deferred is None or not deferred.HasField("source"):
        return None
    source = deferred.source
    edition_name = source.edition.name if source.HasField("edition") else ""
    root_hex = source.root.value.hex() if source.HasField("root") else ""
    return f"{edition_name}:{source.domain}:{root_hex}:{deferred.source_seq}"


def decode_event(page: EventPage, full_type_name: str, msg_class: type[T]) -> T | None:
    """Decode a page's event payload if its type URL matches the given name.

    Runtime-name dispatch — ``full_type_name`` is the fully-qualified
    proto name (e.g. ``"orders.OrderCreated"``). Compared exactly against
    ``TYPE_URL_PREFIX + full_type_name``. The class-keyed shortcut is
    ``EventPage(page).decode_typed(msg_class)`` (derives the name from
    ``msg_class.DESCRIPTOR.full_name``); use this free function when the
    type name is a runtime variable.
    """
    if page is None or not page.HasField("event"):
        return None
    if page.event.type_url != TYPE_URL_PREFIX + full_type_name:
        return None
    try:
        msg = msg_class()
        page.event.Unpack(msg)
        return msg
    except Exception:
        return None


# Destination map (cross-language alias for Rust's `proto_ext::destination_map`)


def destination_map(destinations: list[EventBook]) -> dict[str, EventBook]:
    """Build a map from root UUID hex to EventBook for destination lookup.

    Used in multi-destination sagas to look up the correct EventBook
    by aggregate root when setting command sequences. Entries without
    a root are skipped.
    """
    result: dict[str, EventBook] = {}
    for dest in destinations:
        if not dest.HasField("cover") or not dest.cover.HasField("root"):
            continue
        result[dest.cover.root.value.hex()] = dest
    return result


# Type URL helpers


def wire_name(full_name: str) -> str:
    """Cross-language alias — identity in Python.

    Audit finding #32. Python's protoc emits ``DESCRIPTOR.full_name`` and
    ``Pack()`` type URLs with the full ``angzarr_client.proto.*`` package
    prefix already, so no stripping is needed. The shim exists so
    cross-language code can call ``wire_name`` in either language.
    """
    return full_name


def type_url(type_name: str) -> str:
    """Construct a full type URL from a fully-qualified message type name."""
    return f"{TYPE_URL_PREFIX}{wire_name(type_name)}"


def type_name_from_url(type_url_str: str) -> str:
    """Extract the wire-format type name from a type URL.

    Returns the part after the last ``/``. Inputs with no slash are
    returned unchanged.
    """
    if "/" in type_url_str:
        return type_url_str.rsplit("/", 1)[1]
    return type_url_str


def type_url_matches(type_url_str: str, type_name: str) -> bool:
    """Check if a type URL matches the given fully-qualified type name."""
    return type_url_str == TYPE_URL_PREFIX + wire_name(type_name)


# Alias for Rust API consistency
type_url_matches_exact = type_url_matches


# Type-safe reflection helpers


def type_matches(any_proto: ProtoAny, msg_class: type[T]) -> bool:
    """Check if an Any contains a message of the given type using DESCRIPTOR."""
    if any_proto is None:
        return False
    return any_proto.Is(msg_class.DESCRIPTOR)


def try_unpack(any_proto: ProtoAny, msg_class: type[T]) -> T | None:
    """Unpack an Any to msg_class if type matches, returning None otherwise."""
    if not type_matches(any_proto, msg_class):
        return None
    try:
        msg = msg_class()
        any_proto.Unpack(msg)
        return msg
    except Exception:
        return None


def unpack(any_proto: ProtoAny, msg_class: type[T]) -> T:
    """Unpack an Any to msg_class, raising ValueError if type doesn't match."""
    if not type_matches(any_proto, msg_class):
        expected = full_type_name(msg_class)
        raise ValueError(
            f"type mismatch: expected {expected}, got {any_proto.type_url}"
        )
    msg = msg_class()
    any_proto.Unpack(msg)
    return msg


def full_type_name(msg_class: type[Message]) -> str:
    """Get the fully-qualified type name from a message class DESCRIPTOR."""
    return msg_class.DESCRIPTOR.full_name


def full_type_url_for(msg_class: type[Message]) -> str:
    """Get the full type URL for a message class."""
    return TYPE_URL_PREFIX + full_type_name(msg_class)


# Alias for Rust API consistency
full_type_url = full_type_url_for


# Timestamp helpers


def now() -> Timestamp:
    """Return the current time as a protobuf Timestamp."""
    ts = Timestamp()
    # Timestamp.GetCurrentTime() internally calls datetime.datetime.utcnow()
    # which is deprecated in Python 3.12+. Use FromDatetime with a tz-aware
    # UTC value instead — same wire result, no deprecation warning.
    ts.FromDatetime(datetime.now(timezone.utc))
    return ts


def parse_timestamp(rfc3339: str) -> Timestamp:
    """Parse an RFC3339 timestamp string."""
    try:
        ts = Timestamp()
        ts.FromJsonString(rfc3339)
        return ts
    except ValueError as e:
        raise InvalidTimestampError(str(e)) from e


# Construction helpers (build proto messages from primitives)


def new_cover(
    domain_name: str,
    root: PyUUID,
    correlation_id_val: str = "",
    edition_val: Edition | None = None,
) -> Cover:
    """Create a new Cover with the given parameters."""
    cover = Cover(
        domain=domain_name,
        root=uuid_to_proto(root),
        correlation_id=correlation_id_val,
    )
    if edition_val is not None:
        cover.edition.CopyFrom(edition_val)
    return cover


def new_command_page(sequence: int, command: ProtoAny) -> CommandPage:
    """Create a command page from a sequence and Any message."""
    page = CommandPage()
    page.header.sequence = sequence
    page.command.CopyFrom(command)
    return page


def new_command_book(cover: Cover, pages: list[CommandPage]) -> CommandBook:
    """Create a CommandBook with the given cover and pages."""
    book = CommandBook()
    book.cover.CopyFrom(cover)
    book.pages.extend(pages)
    return book


def range_selection(lower: int, upper: int | None = None) -> SequenceRange:
    """Create a sequence range selection."""
    r = SequenceRange(lower=lower)
    if upper is not None:
        r.upper = upper
    return r


def temporal_by_sequence(seq: int) -> TemporalQuery:
    """Create a temporal selection as-of a sequence."""
    return TemporalQuery(as_of_sequence=seq)


def temporal_by_time(ts: Timestamp) -> TemporalQuery:
    """Create a temporal selection as-of a timestamp."""
    tq = TemporalQuery()
    tq.as_of_time.CopyFrom(ts)
    return tq


def correlated_metadata(correlation_id: str) -> list[tuple[str, str]]:
    """Build gRPC metadata carrying the ``x-correlation-id`` header.

    Returns the list shape grpcio expects on stub calls. Empty
    correlation IDs produce an empty list — the header is skipped, never
    sent as a blank value. Audit #69.
    """
    if not correlation_id:
        return []
    return [(CORRELATION_ID_HEADER, correlation_id)]


# Suppress unused-import warnings for re-exports that are imported only
# to make `from .helpers import Query` work for users who need the
# proto type. Query/PageHeader stay accessible through the proto path
# but exporting a couple here for stable backwards-compat with code
# that imports proto types from helpers historically.
_ = (Query, PageHeader)
