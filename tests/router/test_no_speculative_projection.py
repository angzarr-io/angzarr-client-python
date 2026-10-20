"""No speculative projection: what-if execution is an edition, never a
speculative RPC, so the router binding offers none: a projector dispatch is
always live."""

from __future__ import annotations

import dataclasses

import pytest
from google.protobuf import wrappers_pb2

from angzarr_client.proto.io.angzarr.v1 import types_pb2
from angzarr_client.router import PageContext, ProjectorDispatch, Router, pack


def _projector(writes: list[int]) -> ProjectorDispatch:
    dispatch = ProjectorDispatch("CountingProjector", wrappers_pb2.Int64Value)

    def fold(projection, event_any):
        projection.value += wrappers_pb2.Int64Value.FromString(event_any.value).value
        writes.append(projection.value)

    dispatch.on_event("google.protobuf.Int64Value", fold)
    return dispatch


def _book(*values: int) -> types_pb2.EventBook:
    book = types_pb2.EventBook()
    book.cover.domain = "source"
    for seq, value in enumerate(values):
        page = book.pages.add()
        page.sequence = seq
        page.payload.CopyFrom(pack(wrappers_pb2.Int64Value(value=value)))
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
    assert projection.projection == pack(wrappers_pb2.Int64Value(value=5))
    assert writes == [2, 5]
