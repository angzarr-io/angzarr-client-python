"""Tests for event packing utilities.

Audit finding #57: the previous high-level ``pack_event(cover, event, seq, ...)``
and ``pack_events(cover, events, start_seq, ...)`` helpers were deleted
(no production callers; same shape expressible as
``new_event_book(cmd_book, seq, packed_any)`` after a direct ``Any.Pack``).
The remaining ``new_event_book*`` helpers are still tested below.
"""

from google.protobuf.any_pb2 import Any
from google.protobuf.timestamp_pb2 import Timestamp

from angzarr_client.event_packing import new_event_book, new_event_book_multi
from angzarr_client.proto.angzarr import types_pb2 as angzarr


def test_new_event_book_single_packed_event():
    cmd = angzarr.CommandBook()
    cmd.cover.root.CopyFrom(angzarr.UUID(value=b"test-root"))
    packed = Any()
    packed.Pack(Timestamp(seconds=5))
    book = new_event_book(cmd, seq=3, event=packed)
    assert len(book.pages) == 1
    assert book.pages[0].header.sequence == 3
    assert book.pages[0].event.type_url == packed.type_url
    assert book.cover.root.value == b"test-root"


def test_new_event_book_multi_sequential():
    cmd = angzarr.CommandBook()
    cmd.cover.root.CopyFrom(angzarr.UUID(value=b"r"))
    packed = []
    for i in range(3):
        a = Any()
        a.Pack(Timestamp(seconds=i))
        packed.append(a)
    book = new_event_book_multi(cmd, start_seq=10, events=packed)
    assert len(book.pages) == 3
    for i, page in enumerate(book.pages):
        assert page.header.sequence == 10 + i


def test_new_event_book_multi_empty():
    cmd = angzarr.CommandBook()
    cmd.cover.root.CopyFrom(angzarr.UUID(value=b"r"))
    book = new_event_book_multi(cmd, start_seq=0, events=[])
    assert len(book.pages) == 0
