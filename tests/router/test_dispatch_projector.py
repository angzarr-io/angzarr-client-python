"""R13: projector dispatch.

Projectors consume events from one or more source domains and produce side
effects (DB writes, HTTP calls, external output). Contract:

  - Projector class decorated with ``@projector(name, domains)``.
  - ``@handles(Evt)`` method receives ``(event)`` — no state, no destinations.
  - Return value is ignored (side-effect semantics).
  - ``ProjectorRouter.dispatch(EventBook)`` iterates each event page and
    fans out to all matching projectors in registration order.
  - Multi-projector fan-out: all matching handlers for a given event type
    are invoked; returns a ``Projection`` (typically with cover metadata).
"""

from __future__ import annotations

from google.protobuf.any_pb2 import Any as ProtoAny

from angzarr_client.helpers import TYPE_URL_PREFIX
from angzarr_client.proto.angzarr import (
    Cover,
    EventBook,
    EventPage,
    PageHeader,
    Projection,
)
from angzarr_client.router import (
    Router,
    handles,
    projector,
)
from tests.fixtures import (
    OrderCompleted,
    OrderCreated,
    StockReserved,
)


def _event_book(msgs: list, domain: str = "order") -> EventBook:
    book = EventBook()
    book.cover.CopyFrom(Cover(domain=domain))
    for offset, m in enumerate(msgs):
        any_evt = ProtoAny()
        any_evt.type_url = TYPE_URL_PREFIX + m.DESCRIPTOR.full_name
        any_evt.value = m.SerializeToString()
        page = EventPage()
        page.header.CopyFrom(PageHeader(sequence=offset))
        page.event.CopyFrom(any_evt)
        book.pages.append(page)
    book.next_sequence = len(msgs)
    return book


# --------------------------------------------------------------------------
# Single-projector dispatch
# --------------------------------------------------------------------------


def test_projector_handles_event_and_writes_side_effects():
    written = []

    @projector(name="prj-output", domains=["order"])
    class Output:
        @handles(OrderCreated)
        def on_created(self, event):
            written.append(("created", event.order_id))

    router = Router("prjs").with_handler(Output, lambda: Output()).build()
    router.dispatch(_event_book([OrderCreated(order_id="o-1")]))

    assert written == [("created", "o-1")]


def test_projector_returns_projection_result():
    @projector(name="prj-x", domains=["order"])
    class P:
        @handles(OrderCreated)
        def on(self, event):
            pass

    router = Router("prjs").with_handler(P, lambda: P()).build()
    result = router.dispatch(_event_book([OrderCreated(order_id="o-1")]))

    # Projection returned, cover copied from source book
    assert isinstance(result, Projection)
    assert result.cover.domain == "order"


def test_projector_synthetic_projection_carries_next_sequence():
    # Audit #63: synthetic Projection carries the input book's
    # next_sequence so downstream readers can correlate the projection
    # back to the event-stream cursor. Mirrors Rust runtime.rs:590-595.

    @projector(name="prj-seq", domains=["order"])
    class P:
        @handles(OrderCreated)
        def on(self, event):
            pass

    router = Router("prjs").with_handler(P, lambda: P()).build()

    # next_sequence > 0 case: 3 events, book.next_sequence = 3
    result = router.dispatch(
        _event_book(
            [
                OrderCreated(order_id="o-1"),
                OrderCreated(order_id="o-2"),
                OrderCreated(order_id="o-3"),
            ]
        )
    )
    assert result.sequence == 3

    # next_sequence == 0 case: empty book is valid; sequence carries through
    book = _event_book([])
    book.next_sequence = 0
    result = router.dispatch(book)
    assert result.sequence == 0


def test_projector_iterates_every_event_in_book():
    written = []

    @projector(name="prj-x", domains=["order"])
    class P:
        @handles(OrderCreated)
        def on(self, event):
            written.append(event.order_id)

    router = Router("prjs").with_handler(P, lambda: P()).build()
    router.dispatch(
        _event_book(
            [
                OrderCreated(order_id="a"),
                OrderCreated(order_id="b"),
                OrderCreated(order_id="c"),
            ]
        )
    )

    assert written == ["a", "b", "c"]


def test_unknown_event_types_skipped_silently():
    written = []

    @projector(name="prj-x", domains=["order"])
    class P:
        @handles(OrderCreated)
        def on(self, event):
            written.append("created")

    router = Router("prjs").with_handler(P, lambda: P()).build()
    # Mix of handled + not-handled events
    router.dispatch(
        _event_book(
            [
                OrderCreated(order_id="a"),
                OrderCompleted(order_id="a"),
                OrderCreated(order_id="b"),
            ]
        )
    )

    # OrderCompleted has no handler → skipped
    assert written == ["created", "created"]


# --------------------------------------------------------------------------
# Multi-projector fan-out
# --------------------------------------------------------------------------


def test_two_projectors_both_invoked_in_registration_order():
    call_order = []

    @projector(name="prj-a", domains=["order"])
    class A:
        @handles(OrderCreated)
        def on(self, event):
            call_order.append(("A", event.order_id))

    @projector(name="prj-b", domains=["order"])
    class B:
        @handles(OrderCreated)
        def on(self, event):
            call_order.append(("B", event.order_id))

    router = (
        Router("prjs").with_handler(A, lambda: A()).with_handler(B, lambda: B()).build()
    )
    router.dispatch(_event_book([OrderCreated(order_id="o-1")]))

    assert call_order == [("A", "o-1"), ("B", "o-1")]


def test_projector_filters_by_declared_domains():
    written = []

    @projector(name="prj-order-only", domains=["order"])
    class OrderOnly:
        @handles(StockReserved)
        def on(self, event):
            # Won't fire — "inventory" not in declared domains
            written.append("fired")

    router = Router("prjs").with_handler(OrderOnly, lambda: OrderOnly()).build()
    # Dispatch an inventory EventBook — projector only declares "order" domain
    router.dispatch(
        _event_book(
            [StockReserved(order_id="a", sku="x", quantity=1)], domain="inventory"
        )
    )

    assert written == []


def test_projector_wildcard_domain_matches_any_book_domain():
    """Audit finding #53: ``domains=["*"]`` is a catch-all that fires for
    every incoming book domain. Mirrors Rust's ``runtime.rs:428``
    (``x == d || x == "*"``)."""
    fired_domains: list[str] = []

    @projector(name="prj-catch-all", domains=["*"])
    class CatchAll:
        @handles(OrderCreated)
        def on_order(self, event):
            fired_domains.append("order_event")

        @handles(StockReserved)
        def on_stock(self, event):
            fired_domains.append("stock_event")

    router = Router("prjs").with_handler(CatchAll, lambda: CatchAll()).build()

    # Dispatch from "order" domain — must fire
    router.dispatch(_event_book([OrderCreated(order_id="o-1")], domain="order"))
    # Dispatch from "inventory" domain — must also fire (catch-all)
    router.dispatch(
        _event_book(
            [StockReserved(order_id="a", sku="x", quantity=1)], domain="inventory"
        )
    )

    assert fired_domains == ["order_event", "stock_event"]


def test_projector_wildcard_alongside_specific_domain_doesnt_double_fire():
    """A projector declaring ``domains=["*", "order"]`` still fires once
    per matching event — the wildcard match short-circuits, no double dispatch."""
    fire_count = {"n": 0}

    @projector(name="prj-mix", domains=["*", "order"])
    class Mix:
        @handles(OrderCreated)
        def on(self, event):
            fire_count["n"] += 1

    router = Router("prjs").with_handler(Mix, lambda: Mix()).build()
    router.dispatch(_event_book([OrderCreated(order_id="o-1")], domain="order"))

    assert fire_count["n"] == 1


# --------------------------------------------------------------------------
# No prior state, no destinations, no response merging
# --------------------------------------------------------------------------


def test_projector_handler_receives_only_event_parameter():
    captured = {}

    @projector(name="prj-x", domains=["order"])
    class P:
        @handles(OrderCreated)
        def on(self, event):
            captured["event"] = event

    router = Router("prjs").with_handler(P, lambda: P()).build()
    router.dispatch(_event_book([OrderCreated(order_id="o-1", customer_id="c-1")]))

    assert captured["event"].order_id == "o-1"
    assert captured["event"].customer_id == "c-1"
