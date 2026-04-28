"""Tests for ``@handles_fact`` dispatch — audit #45.

Covers:
- ``@handles_fact`` decorator stamps the right sentinel.
- ``CommandHandlerRouter.supports_handle_fact()`` correctly reads metadata.
- ``dispatch_fact`` rebuilds state from prior_events and routes facts
  to matching ``@handles_fact`` methods.
- Facts with no matching handler are silently skipped.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from google.protobuf.any_pb2 import Any as ProtoAny

from angzarr_client.helpers import TYPE_URL_PREFIX
from angzarr_client.proto.angzarr import EventBook, EventPage
from angzarr_client.proto.angzarr.command_handler_pb2 import FactRequest
from angzarr_client.router import (
    Router,
    applies,
    command_handler,
    handles,
    handles_fact,
)
from tests.fixtures import (
    CreateOrder,
    OrderCreated,
    StockReserved,
)


@dataclass
class S:
    """Plain dataclass state — enough for dispatch_fact (no Any pack/unpack)."""

    orders: list = field(default_factory=list)
    facts: list = field(default_factory=list)


def _fact_page(msg) -> EventPage:
    page = EventPage()
    any_msg = ProtoAny()
    any_msg.type_url = TYPE_URL_PREFIX + msg.DESCRIPTOR.full_name
    any_msg.value = msg.SerializeToString()
    page.event.CopyFrom(any_msg)
    return page


def _fact_book(msgs: list) -> EventBook:
    book = EventBook()
    for m in msgs:
        book.pages.append(_fact_page(m))
    return book


# --------------------------------------------------------------------------
# Decorator + metadata


def test_handles_fact_decorator_sets_sentinel():
    @handles_fact(StockReserved)
    def fn(self, event, state):
        pass

    assert fn.__angzarr_handles_fact__ is StockReserved


def test_handles_fact_collides_with_other_method_decorators():
    with pytest.raises(TypeError):

        @handles(CreateOrder)
        @handles_fact(StockReserved)
        def fn(self, event, state):
            pass


# --------------------------------------------------------------------------
# supports_handle_fact() gate


def test_supports_handle_fact_false_when_no_fact_handlers():
    @command_handler(domain="order", state=S)
    class Order:
        @handles(CreateOrder)
        def on_create(self, cmd, state, seq):
            return []

    router = Router("orders").with_handler(Order, lambda: Order()).build()
    assert router.supports_handle_fact() is False


def test_supports_handle_fact_true_when_fact_handler_present():
    @command_handler(domain="order", state=S)
    class Order:
        @handles(CreateOrder)
        def on_create(self, cmd, state, seq):
            return []

        @handles_fact(StockReserved)
        def on_stock_reserved(self, event, state):
            state.facts.append(event.order_id)
            return []

    router = Router("orders").with_handler(Order, lambda: Order()).build()
    assert router.supports_handle_fact() is True


# --------------------------------------------------------------------------
# dispatch_fact


def test_dispatch_fact_routes_to_matching_handler():
    seen = []

    @command_handler(domain="order", state=S)
    class Order:
        @handles(CreateOrder)
        def on_create(self, cmd, state, seq):
            return []

        @handles_fact(StockReserved)
        def on_stock_reserved(self, event, state):
            seen.append((event.order_id, list(state.facts)))
            return []

    router = Router("orders").with_handler(Order, lambda: Order()).build()
    request = FactRequest()
    request.facts.CopyFrom(_fact_book([StockReserved(order_id="o-1", quantity=3)]))
    router.dispatch_fact(request)
    assert seen == [("o-1", [])]


def test_dispatch_fact_rebuilds_state_from_prior_events():
    @command_handler(domain="order", state=S)
    class Order:
        @applies(OrderCreated)
        def apply_created(self, state, event):
            state.orders.append(event.order_id)

        @handles(CreateOrder)
        def on_create(self, cmd, state, seq):
            return []

        @handles_fact(StockReserved)
        def on_stock_reserved(self, event, state):
            # State should reflect the prior OrderCreated.
            assert state.orders == ["o-1"], state.orders
            return []

    router = Router("orders").with_handler(Order, lambda: Order()).build()
    request = FactRequest()
    # prior_events: OrderCreated(o-1) was applied.
    request.prior_events.CopyFrom(_fact_book([OrderCreated(order_id="o-1")]))
    request.facts.CopyFrom(_fact_book([StockReserved(order_id="o-1", quantity=3)]))
    router.dispatch_fact(request)


def test_dispatch_fact_emitted_events_concatenate():
    @command_handler(domain="order", state=S)
    class Order:
        @handles(CreateOrder)
        def on_create(self, cmd, state, seq):
            return []

        @handles_fact(StockReserved)
        def on_stock_reserved(self, event, state):
            return [OrderCreated(order_id=event.order_id + "-derived")]

    router = Router("orders").with_handler(Order, lambda: Order()).build()
    request = FactRequest()
    request.facts.CopyFrom(
        _fact_book(
            [
                StockReserved(order_id="o-1", quantity=3),
                StockReserved(order_id="o-2", quantity=5),
            ]
        )
    )
    book = router.dispatch_fact(request)
    assert len(book.pages) == 2


def test_dispatch_fact_unmatched_facts_silently_skipped():
    seen = []

    @command_handler(domain="order", state=S)
    class Order:
        @handles(CreateOrder)
        def on_create(self, cmd, state, seq):
            return []

        @handles_fact(StockReserved)
        def on_stock_reserved(self, event, state):
            seen.append(event.order_id)
            return []

    router = Router("orders").with_handler(Order, lambda: Order()).build()
    request = FactRequest()
    # A CreateOrder fact isn't handled by any @handles_fact — silently skipped.
    request.facts.CopyFrom(_fact_book([CreateOrder(order_id="ignored")]))
    book = router.dispatch_fact(request)
    assert seen == []
    assert len(book.pages) == 0
