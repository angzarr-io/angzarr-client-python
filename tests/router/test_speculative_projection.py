"""Speculative projection: the router binding tells projector handlers a
dispatch is speculative (PageContext.speculative, also on current_page() in
the finisher), and the host routes ProjectorService.HandleSpeculative through
it, so a handler can keep durable / external state untouched."""

from __future__ import annotations

import grpc
import pytest
from google.protobuf import wrappers_pb2

from angzarr_client import ComponentHost
from angzarr_client.proto.io.angzarr.v1 import projector_pb2_grpc, types_pb2
from angzarr_client.router import ProjectorDispatch, Router, current_page, pack


class _ExternalStore:
    """Stands in for a projector's durable read model."""

    def __init__(self) -> None:
        self.writes: list[int] = []
        self.seen: list[tuple[str, bool]] = []


def _projector(store: _ExternalStore) -> ProjectorDispatch:
    dispatch = ProjectorDispatch("CountingProjector", wrappers_pb2.Int64Value)

    def fold(projection, event_any, ctx):
        store.seen.append(("fold", ctx.speculative))
        projection.value += wrappers_pb2.Int64Value.FromString(event_any.value).value
        if not ctx.speculative:
            store.writes.append(projection.value)

    def finish(projection, events):
        store.seen.append(("finish", current_page().speculative))
        proj = types_pb2.Projection(projector="counting", sequence=projection.value)
        proj.cover.CopyFrom(events.cover)
        return proj

    dispatch.on_event_with_context("google.protobuf.Int64Value", fold)
    dispatch.finish(finish)
    return dispatch


def _book(*values: int) -> types_pb2.EventBook:
    book = types_pb2.EventBook()
    book.cover.domain = "source"
    for seq, value in enumerate(values):
        page = book.pages.add()
        page.header.sequence = seq
        page.event.CopyFrom(pack(wrappers_pb2.Int64Value(value=value)))
    return book


def test_binding_marks_a_speculative_projector_dispatch():
    store = _ExternalStore()
    with Router() as router:
        router.register_projector(_projector(store))
        projection = router.dispatch_projector(_book(2, 3), speculative=True)
    assert projection.sequence == 5
    assert store.writes == []
    assert store.seen == [("fold", True), ("fold", True), ("finish", True)]


def test_binding_projector_dispatch_is_live_by_default():
    store = _ExternalStore()
    with Router() as router:
        router.register_projector(_projector(store))
        projection = router.dispatch_projector(_book(2, 3))
    assert projection.sequence == 5
    assert store.writes == [2, 5]
    assert store.seen == [("fold", False), ("fold", False), ("finish", False)]


def test_page_context_is_live_unless_marked():
    from angzarr_client.router import PageContext

    assert PageContext().speculative is False
    assert PageContext(speculative=True).speculative is True


@pytest.fixture
def channel():
    store = _ExternalStore()
    host = ComponentHost(Router())
    host.add_projector(_projector(store))
    address = host.start("127.0.0.1:0")
    ch = grpc.insecure_channel(address)
    yield ch, store
    ch.close()
    host.stop(grace=0)


def test_host_handle_speculative_leaves_external_state_untouched(channel):
    ch, store = channel
    stub = projector_pb2_grpc.ProjectorServiceStub(ch)
    speculative = stub.HandleSpeculative(_book(4, 1), timeout=5)
    assert (speculative.projector, speculative.sequence) == ("counting", 5)
    assert speculative.cover.domain == "source"
    assert store.writes == []
    assert {flag for _, flag in store.seen} == {True}

    live = stub.Handle(_book(4, 1), timeout=5)
    assert live == speculative
    assert store.writes == [4, 5]
    assert store.seen[-3:] == [("fold", False), ("fold", False), ("finish", False)]
