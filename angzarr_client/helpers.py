"""Pure utility helpers for working with Angzarr proto types.

Cover/book/page accessors live on the wrapper classes in
``angzarr_client.wrappers`` (e.g. ``Cover.domain()``,
``EventBook.next_sequence()``). What stays here are functions that
either don't take a wrapped proto, take a primitive, or build a new
proto from scratch.
"""

from typing import TypeVar
from uuid import UUID as PyUUID

from google.protobuf.any_pb2 import Any as ProtoAny
from google.protobuf.message import Message
from google.protobuf.timestamp_pb2 import Timestamp

from ._pb import (
    UUID,
    Command,
    CommandBook,
    CommandPage,
    Cover,
    EventPage,
    SequenceRange,
    TemporalQuery,
)
from .error_codes import codes, keys, messages
from .errors import InvalidArgumentError, InvalidTimestampError

# Type variable for generic message type
T = TypeVar("T", bound=Message)

# Constants matching Rust proto_ext::constants
UNKNOWN_DOMAIN = "unknown"
WILDCARD_DOMAIN = "*"
DEFAULT_EDITION = ""
META_ANGZARR_DOMAIN = "_angzarr"
PROJECTION_DOMAIN_PREFIX = "_projection"
PROJECTION_TYPE_URL = "io.angzarr.v1.Projection"
CORRELATION_ID_HEADER = "x-correlation-id"
# Emitted Any type URLs are "/" + the fully-qualified message name; any
# prefix is accepted on input, the name being the text after the last "/".
TYPE_URL_PREFIX = "/"


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


def decode_event(page: EventPage, full_type_name: str, msg_class: type[T]) -> T | None:
    """Decode a page's event payload if its type URL matches the given name.

    Runtime-name dispatch — ``full_type_name`` is the fully-qualified
    proto name (e.g. ``"my.pkg.v1.SomethingHappened"``), compared exactly with the
    name after the type URL's last ``/`` (any prefix). The class-keyed shortcut is
    ``EventPage(page).decode_typed(msg_class)`` (derives the name from
    ``msg_class.DESCRIPTOR.full_name``); use this free function when the
    type name is a runtime variable.
    """
    if page is None or not page.HasField("payload"):
        return None
    if not type_url_matches(page.payload.type_url, full_type_name):
        return None
    try:
        msg = msg_class()
        page.payload.Unpack(msg)
        return msg
    except Exception:  # noqa: BLE001 — undecodable payload decodes to None
        return None


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
    """Whether a type URL names ``type_name``: the text after its last ``/``
    (whatever the prefix) equals the fully-qualified name exactly."""
    return type_name_from_url(type_url_str) == wire_name(type_name)


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
    except Exception:  # noqa: BLE001 — undecodable payload decodes to None
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
    ts.GetCurrentTime()
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


def new_cover(domain_name: str, root: PyUUID, edition: str | None = None) -> Cover:
    """Create a Cover for the stream (domain, edition, root); ``edition``
    None is the main timeline."""
    cover = Cover(domain=domain_name, root=uuid_to_proto(root))
    if edition is not None:
        cover.edition = edition
    return cover


def new_command_page(command: ProtoAny) -> CommandPage:
    """Create a command page whose Command arm carries ``command``."""
    return CommandPage(command=Command(payload=command))


def new_command_book(cover: Cover, page: CommandPage) -> CommandBook:
    """Create a CommandBook addressing ``cover`` with its single ``page``."""
    book = CommandBook()
    book.cover.CopyFrom(cover)
    book.page.CopyFrom(page)
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


def correlated_metadata(correlation_id: str | UUID | None) -> list[tuple[str, str]]:
    """Build gRPC metadata carrying the ``x-correlation-id`` header.

    ``correlation_id`` is the header text, or a proto UUID rendered in its
    standard text form. Returns the list shape grpcio expects on stub
    calls. An empty or unset correlation ID produces an empty list — the
    header is skipped, never sent as a blank value. Audit #69.
    """
    if isinstance(correlation_id, UUID):
        correlation_id = proto_uuid_to_text(correlation_id)
    if not correlation_id:
        return []
    return [(CORRELATION_ID_HEADER, correlation_id)]


def as_uuid(value: PyUUID | str, field: str) -> PyUUID:
    """``value`` as a UUID: a UUID as given, or a UUID's text form parsed.
    Anything else is refused with ``PROTO_UUID_INVALID``, naming ``field``."""
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
