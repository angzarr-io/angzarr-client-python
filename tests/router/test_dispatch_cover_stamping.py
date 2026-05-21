"""Tests that the dispatch boundary stamps the request's Cover onto any
``CommandRejectedError`` raised by a handler.

Handlers raise ``CommandRejectedError`` (or a typed subclass) without
threading the addressing envelope through their signatures; the router
attaches the originating ``CommandRequest.cover`` so callers can trace
``(domain, root, correlation_id)`` back without each call site doing it.
"""

from __future__ import annotations

import pytest

from google.protobuf.any_pb2 import Any as ProtoAny

from angzarr_client.errors import CommandRejectedError
from angzarr_client.helpers import TYPE_URL_PREFIX
from angzarr_client.proto.angzarr import (
    CommandBook,
    CommandPage,
    ContextualCommand,
    Cover,
    PageHeader,
    UUID,
)
from angzarr_client.router import Router, command_handler, handles
from tests.fixtures import CreateOrder


def _request_with_cover(
    cmd_msg, *, domain: str, root_bytes: bytes, correlation_id: str
):
    cmd_any = ProtoAny()
    cmd_any.type_url = TYPE_URL_PREFIX + cmd_msg.DESCRIPTOR.full_name
    cmd_any.value = cmd_msg.SerializeToString()
    return ContextualCommand(
        command=CommandBook(
            cover=Cover(
                domain=domain,
                root=UUID(value=root_bytes),
                correlation_id=correlation_id,
            ),
            pages=[CommandPage(header=PageHeader(), command=cmd_any)],
        )
    )


def _build_router_that_rejects():
    @command_handler(domain="order", state=Cover)
    class Order:
        @handles(CreateOrder)
        def on_create(self, cmd, state, seq):
            raise CommandRejectedError.precondition_failed(
                "ORDER_REJECTED",
                "Order rejected for testing",
            )

    return Router("orders").with_handler(Order, lambda: Order()).build()


def test_dispatch_stamps_cover_on_rejection():
    router = _build_router_that_rejects()
    request = _request_with_cover(
        CreateOrder(),
        domain="order",
        root_bytes=b"\x01\x02\x03",
        correlation_id="corr-abc",
    )

    with pytest.raises(CommandRejectedError) as exc_info:
        router.dispatch(request)

    rej = exc_info.value
    assert rej.cover is not None, "router should stamp cover on rejection"
    assert rej.cover.domain == "order"
    assert rej.cover.correlation_id == "corr-abc"
    assert rej.cover.root.value == b"\x01\x02\x03"


def test_dispatch_does_not_overwrite_explicitly_set_cover():
    """A handler that already set its own cover (e.g. mid-saga compensation)
    keeps that cover; the dispatch boundary only stamps when missing."""

    pre_set_cover = Cover(
        domain="elsewhere",
        root=UUID(value=b"\xff"),
        correlation_id="explicit",
    )

    @command_handler(domain="order", state=Cover)
    class Order:
        @handles(CreateOrder)
        def on_create(self, cmd, state, seq):
            err = CommandRejectedError.precondition_failed(
                "ORDER_REJECTED",
                "Order rejected for testing",
            )
            err.cover = pre_set_cover
            raise err

    router = Router("orders").with_handler(Order, lambda: Order()).build()
    request = _request_with_cover(
        CreateOrder(),
        domain="order",
        root_bytes=b"\x01",
        correlation_id="auto",
    )

    with pytest.raises(CommandRejectedError) as exc_info:
        router.dispatch(request)

    assert exc_info.value.cover.domain == "elsewhere"
    assert exc_info.value.cover.correlation_id == "explicit"
