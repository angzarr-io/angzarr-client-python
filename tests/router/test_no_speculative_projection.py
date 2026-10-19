"""No speculative projection: what-if execution is an edition, never a
speculative RPC, so neither the router binding nor the host offers one. A
projector dispatch is always live, and the host leaves
ProjectorService.HandleSpeculative unimplemented."""

from __future__ import annotations

import dataclasses

import grpc
import pytest
from google.protobuf import wrappers_pb2

from angzarr_client import ComponentHost
from angzarr_client.proto.io.angzarr.v1 import projector_pb2_grpc, types_pb2
from angzarr_client.router import PageContext, ProjectorDispatch, Router, pack


def _projector(writes: list[int]) -> ProjectorDispatch:
    dispatch = ProjectorDispatch("CountingProjector", wrappers_pb2.Int64Value)

    def fold(projection, event_any):
        projection.value += wrappers_pb2.Int64Value.FromString(event_any.value).value
        writes.append(projection.value)

    def finish(projection, events):
        proj = types_pb2.Projection(projector="counting", sequence=projection.value)
        proj.cover.CopyFrom(events.cover)
        return proj

    dispatch.on_event("google.protobuf.Int64Value", fold)
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


def test_page_context_carries_no_speculative_flag():
    assert "speculative" not in {f.name for f in dataclasses.fields(PageContext)}


def test_binding_projector_dispatch_takes_no_speculative_option():
    writes: list[int] = []
    with Router() as router:
        router.register_projector(_projector(writes))
        with pytest.raises(TypeError):
            router.dispatch_projector(_book(2), speculative=True)
        projection = router.dispatch_projector(_book(2, 3))
    assert projection.sequence == 5
    assert writes == [2, 5]


def test_host_leaves_handle_speculative_unimplemented():
    host = ComponentHost(Router())
    host.add_projector(_projector([]))
    address = host.start("127.0.0.1:0")
    try:
        with grpc.insecure_channel(address) as ch:
            stub = projector_pb2_grpc.ProjectorServiceStub(ch)
            with pytest.raises(grpc.RpcError) as err:
                stub.HandleSpeculative(_book(1), timeout=5)
            assert err.value.code() == grpc.StatusCode.UNIMPLEMENTED
            assert stub.Handle(_book(1), timeout=5).sequence == 1
    finally:
        host.stop(grace=0)
