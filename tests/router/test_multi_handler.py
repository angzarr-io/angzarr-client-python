"""R8: multi-handler dispatch with output merging.

When two or more instances in the same domain both register @handles for the
same message type, the Router invokes all of them in registration order and
concatenates emitted events into a single EventBook.

Each instance rebuilds its own state independently; mutations on one
instance's state never affect another's.
"""

from __future__ import annotations

from dataclasses import dataclass

from google.protobuf.any_pb2 import Any as ProtoAny

from angzarr_client.helpers import TYPE_URL_PREFIX
from angzarr_client.proto.angzarr import (
    CommandBook,
    CommandPage,
    ContextualCommand,
    Cover,
    EventBook,
    EventPage,
    PageHeader,
)
from angzarr_client.router import (
    Router,
    applies,
    command_handler,
    handles,
)
from tests.fixtures import (
    CreateOrder,
    OrderCompleted,
    OrderCreated,
    StockUpdated,
)


@dataclass
class StateA:
    count: int = 0


@dataclass
class StateB:
    count: int = 0


def _request(
    cmd, prior: list | None = None, domain: str = "order"
) -> ContextualCommand:
    prior = prior or []
    book = EventBook()
    book.cover.CopyFrom(Cover(domain=domain))
    for offset, evt in enumerate(prior):
        page = EventPage()
        page.header.CopyFrom(PageHeader(sequence=offset))
        any_msg = ProtoAny()
        any_msg.type_url = TYPE_URL_PREFIX + evt.DESCRIPTOR.full_name
        any_msg.value = evt.SerializeToString()
        page.event.CopyFrom(any_msg)
        book.pages.append(page)
    book.next_sequence = len(prior)

    any_cmd = ProtoAny()
    any_cmd.type_url = TYPE_URL_PREFIX + cmd.DESCRIPTOR.full_name
    any_cmd.value = cmd.SerializeToString()
    cpage = CommandPage()
    cpage.header.CopyFrom(PageHeader(sequence=len(prior)))
    cpage.command.CopyFrom(any_cmd)
    cbook = CommandBook()
    cbook.cover.CopyFrom(Cover(domain=domain))
    cbook.pages.append(cpage)

    req = ContextualCommand()
    req.command.CopyFrom(cbook)
    req.events.CopyFrom(book)
    return req


# --------------------------------------------------------------------------
# Both handlers invoked; events merged in registration order
# --------------------------------------------------------------------------


def test_both_handlers_invoked_in_registration_order():
    call_order = []

    @command_handler(domain="order", state=StateA)
    class First:
        @handles(CreateOrder)
        def on(self, cmd, state, seq):
            call_order.append("first")
            return OrderCreated(order_id=cmd.order_id, customer_id="from-first")

    @command_handler(domain="order", state=StateB)
    class Second:
        @handles(CreateOrder)
        def on(self, cmd, state, seq):
            call_order.append("second")
            return OrderCreated(order_id=cmd.order_id, customer_id="from-second")

    router = Router("agg").with_handler(First()).with_handler(Second()).build()
    router.dispatch(_request(CreateOrder(order_id="o-1")))

    assert call_order == ["first", "second"]


def test_events_concatenated_in_registration_order():
    @command_handler(domain="order", state=StateA)
    class First:
        @handles(CreateOrder)
        def on(self, cmd, state, seq):
            return OrderCreated(order_id=cmd.order_id, customer_id="first")

    @command_handler(domain="order", state=StateB)
    class Second:
        @handles(CreateOrder)
        def on(self, cmd, state, seq):
            return OrderCompleted(order_id=cmd.order_id, shipped_at="t")

    router = Router("agg").with_handler(First()).with_handler(Second()).build()
    response = router.dispatch(_request(CreateOrder(order_id="o-1")))

    pages = response.events.pages
    assert len(pages) == 2
    # First emitted by First (OrderCreated), then Second (OrderCompleted).
    assert pages[0].event.type_url.endswith("OrderCreated")
    assert pages[1].event.type_url.endswith("OrderCompleted")


def test_reversed_registration_reverses_output_order():
    @command_handler(domain="order", state=StateA)
    class First:
        @handles(CreateOrder)
        def on(self, cmd, state, seq):
            return OrderCreated(order_id=cmd.order_id)

    @command_handler(domain="order", state=StateB)
    class Second:
        @handles(CreateOrder)
        def on(self, cmd, state, seq):
            return OrderCompleted(order_id=cmd.order_id)

    # Register Second before First
    router = Router("agg").with_handler(Second()).with_handler(First()).build()
    response = router.dispatch(_request(CreateOrder(order_id="o-1")))

    pages = response.events.pages
    assert len(pages) == 2
    assert pages[0].event.type_url.endswith("OrderCompleted")
    assert pages[1].event.type_url.endswith("OrderCreated")


# --------------------------------------------------------------------------
# State isolation across instances
# --------------------------------------------------------------------------


def test_each_instance_rebuilds_its_own_state():
    states_seen = []

    @command_handler(domain="order", state=StateA)
    class First:
        @applies(OrderCreated)
        def apply(self, state, event):
            state.count += 1

        @handles(OrderCompleted)
        def on(self, cmd, state, seq):
            states_seen.append(("first", state.count))
            return None

    @command_handler(domain="order", state=StateB)
    class Second:
        @applies(StockUpdated)
        def apply(self, state, event):
            state.count += 100

        @handles(OrderCompleted)
        def on(self, cmd, state, seq):
            states_seen.append(("second", state.count))
            return None

    router = Router("agg").with_handler(First()).with_handler(Second()).build()

    # Prior events: 3 OrderCreated (applies to First only), 2 StockUpdated (applies to Second only)
    prior = [
        OrderCreated(order_id="a"),
        OrderCreated(order_id="b"),
        StockUpdated(sku="s", quantity=1),
        OrderCreated(order_id="c"),
        StockUpdated(sku="t", quantity=2),
    ]
    router.dispatch(_request(OrderCompleted(order_id="a"), prior=prior))

    assert ("first", 3) in states_seen  # First saw 3 OrderCreated
    assert ("second", 200) in states_seen  # Second saw 2 StockUpdated


def test_handler_emitting_none_does_not_contribute_pages():
    @command_handler(domain="order", state=StateA)
    class NoEmit:
        @handles(CreateOrder)
        def on(self, cmd, state, seq):
            return None  # side-effect only

    @command_handler(domain="order", state=StateB)
    class Emits:
        @handles(CreateOrder)
        def on(self, cmd, state, seq):
            return OrderCreated(order_id=cmd.order_id)

    router = Router("agg").with_handler(NoEmit()).with_handler(Emits()).build()
    response = router.dispatch(_request(CreateOrder(order_id="o-1")))

    # Only the Emits handler contributed a page.
    assert len(response.events.pages) == 1
    assert response.events.pages[0].event.type_url.endswith("OrderCreated")


def test_handler_emitting_tuple_yields_multiple_pages():
    @command_handler(domain="order", state=StateA)
    class Multi:
        @handles(CreateOrder)
        def on(self, cmd, state, seq):
            return (
                OrderCreated(order_id=cmd.order_id),
                OrderCompleted(order_id=cmd.order_id),
            )

    router = Router("agg").with_handler(Multi()).build()
    response = router.dispatch(_request(CreateOrder(order_id="o-1")))

    pages = response.events.pages
    assert len(pages) == 2
    assert pages[0].event.type_url.endswith("OrderCreated")
    assert pages[1].event.type_url.endswith("OrderCompleted")


def test_only_matching_handlers_invoked():
    call_order = []

    @command_handler(domain="order", state=StateA)
    class HandlesCreate:
        @handles(CreateOrder)
        def on(self, cmd, state, seq):
            call_order.append("create")
            return None

    @command_handler(domain="order", state=StateB)
    class HandlesComplete:
        @handles(OrderCompleted)
        def on(self, cmd, state, seq):
            call_order.append("complete")
            return None

    router = (
        Router("agg")
        .with_handler(HandlesCreate())
        .with_handler(HandlesComplete())
        .build()
    )
    router.dispatch(_request(CreateOrder(order_id="o-1")))

    assert call_order == ["create"]
