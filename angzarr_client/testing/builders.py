"""Proto message builders for testing.

Provides simplified builders for constructing EventBook, CommandBook,
Cover, and related proto types in tests.
"""

from google.protobuf.any_pb2 import Any as ProtoAny
from google.protobuf.message import Message
from google.protobuf.timestamp_pb2 import Timestamp

from .._pb import (
    UUID,
    Command,
    CommandBook,
    CommandPage,
    Cover,
    EventBook,
    EventMeta,
    EventPage,
)
from ..helpers import TYPE_URL_PREFIX
from ..helpers import now as _now


def make_timestamp() -> Timestamp:
    """Create a timestamp for now.

    Alias for angzarr_client.helpers.now() for backwards compatibility.

    Returns:
        Current time as protobuf Timestamp
    """
    return _now()


def pack_event(msg: Message) -> ProtoAny:
    """Pack a protobuf message into an `Any` with the canonical type URL.

    The type URL is ``TYPE_URL_PREFIX`` ("/") + the message's fully-qualified
    name, the form every angzarr client emits. Mirrors Rust's
    ``testing::builders::pack_event<M>(msg)``.

    Returns:
        ProtoAny containing the packed message.
    """
    event_any = ProtoAny()
    event_any.Pack(msg, type_url_prefix=TYPE_URL_PREFIX)
    return event_any


def make_cover(domain: str, root: bytes) -> Cover:
    """Create a Cover from domain and root bytes.

    Args:
        domain: The aggregate domain name
        root: The aggregate root as 16 bytes

    Returns:
        Cover proto with domain and root set (covers carry no correlation:
        it is recorded per page)
    """
    return Cover(domain=domain, root=UUID(value=root))


def make_event_page(
    sequence: int,
    event: ProtoAny,
) -> EventPage:
    """Create an EventPage.

    Args:
        sequence: The event sequence number
        event: The packed event (ProtoAny)

    Returns:
        EventPage proto, recorded now
    """
    return EventPage(
        payload=event, sequence=sequence, meta=EventMeta(recorded_at=_now())
    )


def make_event_book(
    cover: Cover,
    pages: list[EventPage] | None = None,
    next_sequence: int | None = None,
) -> EventBook:
    """Create an EventBook.

    Args:
        cover: The Cover identifying the aggregate
        pages: List of EventPages (defaults to empty)
        next_sequence: Next sequence number (defaults to len(pages))

    Returns:
        EventBook proto
    """
    pages = pages or []
    return EventBook(
        cover=cover,
        pages=pages,
        next_sequence=next_sequence if next_sequence is not None else len(pages),
    )


def make_command_page(command: ProtoAny) -> CommandPage:
    """Create a CommandPage whose Command arm carries ``command``.

    Args:
        command: The packed command (ProtoAny)

    Returns:
        CommandPage proto
    """
    return CommandPage(command=Command(payload=command))


def make_command_book(cover: Cover, command: ProtoAny) -> CommandBook:
    """Create a CommandBook carrying a single command.

    Args:
        cover: The Cover identifying the target aggregate
        command: The packed command (ProtoAny)

    Returns:
        CommandBook proto with its one page
    """
    return CommandBook(cover=cover, page=make_command_page(command))


__all__ = [
    "make_command_book",
    "make_command_page",
    "make_cover",
    "make_event_book",
    "make_event_page",
    "make_timestamp",
    "pack_event",
]
