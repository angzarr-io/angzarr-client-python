"""R9: sequence numbers increment monotonically across multi-handler dispatch.

Contract: if handler A is invoked with seq=5 and emits 2 events, handler B
(next in registration order) is invoked with seq=7. Emitted pages carry
sequences [5, 6] for A and [7] for B, monotonically increasing across the
merged output.

Single-handler seq behavior was already covered in R6; this round pins the
across-handler contract.
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
    command_handler,
    handles,
)
from tests.fixtures import (
    CreateOrder,
    OrderCompleted,
    OrderCreated,
)


@dataclass
class S:
    pass


def _request_with_next_seq(
    next_seq: int, cmd, domain: str = "order"
) -> ContextualCommand:
    book = EventBook()
    book.cover.CopyFrom(Cover(domain=domain))
    book.next_sequence = next_seq

    any_cmd = ProtoAny()
    any_cmd.type_url = TYPE_URL_PREFIX + cmd.DESCRIPTOR.full_name
    any_cmd.value = cmd.SerializeToString()

    cpage = CommandPage()
    cpage.header.CopyFrom(PageHeader(sequence=next_seq))
    cpage.command.CopyFrom(any_cmd)

    cbook = CommandBook()
    cbook.cover.CopyFrom(Cover(domain=domain))
    cbook.pages.append(cpage)

    req = ContextualCommand()
    req.command.CopyFrom(cbook)
    req.events.CopyFrom(book)
    return req


# --------------------------------------------------------------------------
# Seq passed to each handler
# --------------------------------------------------------------------------


def test_second_handler_sees_seq_advanced_by_first_emission():
    seen = []

    @command_handler(domain="order", state=S)
    class First:
        @handles(CreateOrder)
        def on(self, cmd, state, seq):
            seen.append(("first", seq))
            # Emits 2 events
            return (OrderCreated(order_id="a"), OrderCompleted(order_id="a"))

    @command_handler(domain="order", state=S)
    class Second:
        @handles(CreateOrder)
        def on(self, cmd, state, seq):
            seen.append(("second", seq))
            return OrderCreated(order_id="b")

    router = (
        Router("agg")
        .with_handler(First, lambda: First())
        .with_handler(Second, lambda: Second())
        .build()
    )
    router.dispatch(_request_with_next_seq(5, CreateOrder(order_id="x")))

    assert seen == [("first", 5), ("second", 7)]


def test_third_handler_sees_seq_advanced_by_sum_of_prior_emissions():
    seen = []

    @command_handler(domain="order", state=S)
    class A:
        @handles(CreateOrder)
        def on(self, cmd, state, seq):
            seen.append(seq)
            return OrderCreated(order_id="1")  # 1 event

    @command_handler(domain="order", state=S)
    class B:
        @handles(CreateOrder)
        def on(self, cmd, state, seq):
            seen.append(seq)
            return (
                OrderCreated(order_id="2"),
                OrderCompleted(order_id="2"),
            )  # 2 events

    @command_handler(domain="order", state=S)
    class C:
        @handles(CreateOrder)
        def on(self, cmd, state, seq):
            seen.append(seq)
            return OrderCreated(order_id="3")

    router = (
        Router("agg")
        .with_handler(A, lambda: A())
        .with_handler(B, lambda: B())
        .with_handler(C, lambda: C())
        .build()
    )
    router.dispatch(_request_with_next_seq(10, CreateOrder(order_id="x")))

    # A: 10 → emits 1 → B: 11 → emits 2 → C: 13
    assert seen == [10, 11, 13]


# --------------------------------------------------------------------------
# Emitted pages carry monotonic seqs in merged output
# --------------------------------------------------------------------------


def test_emitted_pages_carry_monotonic_sequences():
    @command_handler(domain="order", state=S)
    class First:
        @handles(CreateOrder)
        def on(self, cmd, state, seq):
            return (OrderCreated(order_id="a"), OrderCompleted(order_id="a"))

    @command_handler(domain="order", state=S)
    class Second:
        @handles(CreateOrder)
        def on(self, cmd, state, seq):
            return OrderCreated(order_id="b")

    router = (
        Router("agg")
        .with_handler(First, lambda: First())
        .with_handler(Second, lambda: Second())
        .build()
    )
    response = router.dispatch(_request_with_next_seq(5, CreateOrder(order_id="x")))

    seqs = [p.header.sequence for p in response.events.pages]
    assert seqs == [5, 6, 7]


def test_handler_emitting_no_events_does_not_advance_seq():
    seen = []

    @command_handler(domain="order", state=S)
    class First:
        @handles(CreateOrder)
        def on(self, cmd, state, seq):
            seen.append(("first", seq))
            return None

    @command_handler(domain="order", state=S)
    class Second:
        @handles(CreateOrder)
        def on(self, cmd, state, seq):
            seen.append(("second", seq))
            return OrderCreated(order_id="b")

    router = (
        Router("agg")
        .with_handler(First, lambda: First())
        .with_handler(Second, lambda: Second())
        .build()
    )
    router.dispatch(_request_with_next_seq(42, CreateOrder(order_id="x")))

    # First emitted nothing → seq unchanged.
    assert seen == [("first", 42), ("second", 42)]


def test_single_handler_seq_matches_next_sequence():
    seen = []

    @command_handler(domain="order", state=S)
    class Only:
        @handles(CreateOrder)
        def on(self, cmd, state, seq):
            seen.append(seq)
            return None

    router = Router("agg").with_handler(Only, lambda: Only()).build()
    router.dispatch(_request_with_next_seq(99, CreateOrder(order_id="x")))
    assert seen == [99]
