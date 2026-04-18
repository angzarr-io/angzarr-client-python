"""Tests for event packing utilities."""

from google.protobuf.timestamp_pb2 import Timestamp

from angzarr_client.event_packing import pack_event, pack_events
from angzarr_client.proto.angzarr import types_pb2 as angzarr


def _cover() -> angzarr.Cover:
    return angzarr.Cover(root=angzarr.UUID(value=b"test-root"))


def test_pack_event_returns_single_page():
    event = Timestamp(seconds=1000)
    book = pack_event(_cover(), event, seq=0)

    assert len(book.pages) == 1
    assert book.cover.root.value == b"test-root"


def test_pack_event_preserves_sequence():
    event = Timestamp(seconds=1000)
    book = pack_event(_cover(), event, seq=42)

    assert book.pages[0].header.sequence == 42


def test_pack_event_sets_created_at():
    event = Timestamp(seconds=1000)
    book = pack_event(_cover(), event, seq=0)

    assert book.pages[0].created_at.seconds > 0


def test_pack_event_packs_event_as_any():
    event = Timestamp(seconds=1000)
    book = pack_event(_cover(), event, seq=0)

    page = book.pages[0]
    assert page.event is not None
    assert "Timestamp" in page.event.type_url


def test_pack_events_multiple_returns_sequential_pages():
    events = [Timestamp(seconds=i) for i in range(3)]
    book = pack_events(_cover(), events, start_seq=5)

    assert len(book.pages) == 3
    for i, page in enumerate(book.pages):
        assert page.header.sequence == 5 + i


def test_pack_events_empty_returns_empty_pages():
    book = pack_events(_cover(), [], start_seq=0)

    assert len(book.pages) == 0
    assert book.cover.root.value == b"test-root"


def test_pack_event_custom_type_url_prefix():
    event = Timestamp(seconds=1000)
    book = pack_event(_cover(), event, seq=0, type_url_prefix="type.custom/")

    assert book.pages[0].event.type_url.startswith("type.custom/")


def test_new_event_book_single_packed_event():
    from google.protobuf.any_pb2 import Any

    from angzarr_client.event_packing import new_event_book

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
    from google.protobuf.any_pb2 import Any

    from angzarr_client.event_packing import new_event_book_multi

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
    from angzarr_client.event_packing import new_event_book_multi

    cmd = angzarr.CommandBook()
    cmd.cover.root.CopyFrom(angzarr.UUID(value=b"r"))
    book = new_event_book_multi(cmd, start_seq=0, events=[])
    assert len(book.pages) == 0
