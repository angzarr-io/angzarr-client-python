"""Event packing utilities for angzarr command handlers.

Wraps protobuf events into EventBook structures with cover, sequence, and timestamp.
"""

from collections.abc import Sequence

from google.protobuf.any_pb2 import Any
from google.protobuf.message import Message
from google.protobuf.timestamp_pb2 import Timestamp

from .helpers import now
from .proto.angzarr import types_pb2 as angzarr


def _now_timestamp() -> Timestamp:
    """Internal alias for now()."""
    return now()


def new_event_book(
    command_book: angzarr.CommandBook,
    seq: int,
    event: Any,
) -> angzarr.EventBook:
    """Create an EventBook with a single pre-packed event.

    Args:
        command_book: The command book (cover is extracted from it).
        seq: The sequence number for this event.
        event: The pre-packed Any event.

    Returns:
        An EventBook containing one page with the event.
    """
    page = angzarr.EventPage(
        event=event,
        created_at=_now_timestamp(),
    )
    page.header.sequence = seq
    return angzarr.EventBook(
        cover=command_book.cover,
        pages=[page],
    )


def new_event_book_multi(
    command_book: angzarr.CommandBook,
    start_seq: int,
    events: Sequence[Any],
) -> angzarr.EventBook:
    """Create an EventBook with multiple pre-packed events.

    Args:
        command_book: The command book (cover is extracted from it).
        start_seq: The starting sequence number.
        events: List of pre-packed Any events.

    Returns:
        An EventBook containing one page per event with sequential numbering.
    """
    now = _now_timestamp()
    pages = []
    for i, event in enumerate(events):
        page = angzarr.EventPage(event=event, created_at=now)
        page.header.sequence = start_seq + i
        pages.append(page)
    return angzarr.EventBook(
        cover=command_book.cover,
        pages=pages,
    )


# Audit finding #57 (Option D): the previous high-level
# `pack_event(cover, event, seq, type_url_prefix) -> EventBook` and
# `pack_events(cover, events, start_seq, type_url_prefix) -> EventBook`
# helpers were deleted. They had no production callers (only their own
# tests in `test_event_packing.py`) and the same shape can be expressed
# as `new_event_book(cmd_book, seq, any_proto)` after packing the event
# with `Any.Pack(msg)` directly. The high-level form was redundant with
# `new_event_book*` and conflicted in name with Rust's low-level
# `pack_event(msg) -> Any`.
