"""R6: single-handler command dispatch.

Exercises ``CommandHandlerRouter.dispatch(ContextualCommand) -> BusinessResponse``:

  - Unpacking the command Any from ``ContextualCommand.command.pages[0]``
  - Routing by type-URL suffix to the registered ``@handles`` method
  - Wrapping the returned event into an ``EventBook`` in ``BusinessResponse``
  - Unknown type_url raises a gRPC ``INVALID_ARGUMENT`` status

State rebuild (R7) and multi-handler merge (R8) are later rounds. R6 uses a
fresh state instance for each dispatch and assumes a single registered
handler.
"""

from __future__ import annotations

from dataclasses import dataclass

import grpc
import pytest
from google.protobuf.any_pb2 import Any as ProtoAny

from angzarr_client.proto.angzarr import (
    CommandBook,
    CommandPage,
    ContextualCommand,
    Cover,
    EventBook,
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
class OrderState:
    created: bool = False


def _make_contextual_command(
    cmd_msg, domain: str = "order", prior: EventBook | None = None, sequence: int = 0
) -> ContextualCommand:
    """Wrap a test proto message into a full ContextualCommand."""
    any_msg = ProtoAny()
    any_msg.type_url = f"type.googleapis.com/{cmd_msg.DESCRIPTOR.full_name}"
    any_msg.value = cmd_msg.SerializeToString()

    page = CommandPage()
    page.header.CopyFrom(PageHeader(sequence=sequence))
    page.command.CopyFrom(any_msg)

    book = CommandBook()
    book.cover.CopyFrom(Cover(domain=domain))
    book.pages.append(page)

    request = ContextualCommand()
    request.command.CopyFrom(book)
    if prior is not None:
        request.events.CopyFrom(prior)
    return request


# --------------------------------------------------------------------------
# Basic dispatch
# --------------------------------------------------------------------------


def test_dispatch_routes_command_to_matching_handler():
    @command_handler(domain="order", state=OrderState)
    class OrderAggregate:
        @handles(CreateOrder)
        def on_create(self, cmd, state, seq):
            return OrderCreated(order_id=cmd.order_id, customer_id=cmd.customer_id)

    router = Router("agg").with_handler(OrderAggregate, lambda: OrderAggregate()).build()

    cmd = CreateOrder(order_id="o-1", customer_id="c-1", items=[])
    request = _make_contextual_command(cmd, domain="order")
    response = router.dispatch(request)

    # Event wrapped into BusinessResponse.events (EventBook)
    assert response.HasField("events")
    assert len(response.events.pages) == 1


def test_dispatch_packs_returned_event_with_correct_type_url():
    @command_handler(domain="order", state=OrderState)
    class OrderAggregate:
        @handles(CreateOrder)
        def on_create(self, cmd, state, seq):
            return OrderCreated(order_id=cmd.order_id, customer_id="x")

    router = Router("agg").with_handler(OrderAggregate, lambda: OrderAggregate()).build()
    request = _make_contextual_command(CreateOrder(order_id="o-1"), domain="order")
    response = router.dispatch(request)

    event_any = response.events.pages[0].event
    assert event_any.type_url.endswith("order.OrderCreated")


def test_dispatch_passes_command_instance_to_handler():
    captured = {}

    @command_handler(domain="order", state=OrderState)
    class OrderAggregate:
        @handles(CreateOrder)
        def on_create(self, cmd, state, seq):
            captured["cmd"] = cmd
            return OrderCreated(order_id=cmd.order_id)

    router = Router("agg").with_handler(OrderAggregate, lambda: OrderAggregate()).build()
    request = _make_contextual_command(
        CreateOrder(order_id="o-1", customer_id="c-1"), domain="order"
    )
    router.dispatch(request)

    assert isinstance(captured["cmd"], CreateOrder)
    assert captured["cmd"].order_id == "o-1"
    assert captured["cmd"].customer_id == "c-1"


def test_dispatch_passes_seq_from_prior_events():
    captured = {}

    @command_handler(domain="order", state=OrderState)
    class OrderAggregate:
        @handles(CreateOrder)
        def on_create(self, cmd, state, seq):
            captured["seq"] = seq
            return OrderCreated(order_id=cmd.order_id)

    router = Router("agg").with_handler(OrderAggregate, lambda: OrderAggregate()).build()

    # Prior EventBook with next_sequence=3
    prior = EventBook()
    prior.next_sequence = 3

    request = _make_contextual_command(CreateOrder(order_id="o-1"), prior=prior)
    router.dispatch(request)

    assert captured["seq"] == 3


def test_dispatch_passes_fresh_state_when_no_prior_events():
    """R6 uses a fresh state (state-factory called once). R7 adds rebuild."""
    captured = {}

    @command_handler(domain="order", state=OrderState)
    class OrderAggregate:
        @handles(CreateOrder)
        def on_create(self, cmd, state, seq):
            captured["state"] = state
            return OrderCreated(order_id=cmd.order_id)

    router = Router("agg").with_handler(OrderAggregate, lambda: OrderAggregate()).build()
    router.dispatch(_make_contextual_command(CreateOrder(order_id="o-1")))

    assert isinstance(captured["state"], OrderState)
    assert captured["state"].created is False


# --------------------------------------------------------------------------
# Unknown type_url → gRPC INVALID_ARGUMENT
# --------------------------------------------------------------------------


def test_unknown_command_type_raises_grpc_invalid_argument():
    @command_handler(domain="order", state=OrderState)
    class OrderAggregate:
        @handles(CreateOrder)
        def on_create(self, cmd, state, seq):
            return OrderCreated(order_id=cmd.order_id)

    router = Router("agg").with_handler(OrderAggregate, lambda: OrderAggregate()).build()

    # Send an OrderCompleted command — no @handles registered for it
    request = _make_contextual_command(OrderCompleted(order_id="o-1"), domain="order")
    with pytest.raises(grpc.RpcError) as exc_info:
        router.dispatch(request)
    # gRPC status code is INVALID_ARGUMENT per the design contract
    assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT  # type: ignore[attr-defined]


def test_unknown_domain_raises_grpc_invalid_argument():
    @command_handler(domain="order", state=OrderState)
    class OrderAggregate:
        @handles(CreateOrder)
        def on_create(self, cmd, state, seq):
            return OrderCreated(order_id=cmd.order_id)

    router = Router("agg").with_handler(OrderAggregate, lambda: OrderAggregate()).build()

    # Correct command type but wrong domain → no match
    request = _make_contextual_command(CreateOrder(order_id="o-1"), domain="shipping")
    with pytest.raises(grpc.RpcError):
        router.dispatch(request)


# --------------------------------------------------------------------------
# BusinessResponse shape: exactly one event when handler returns one
# --------------------------------------------------------------------------


def test_single_event_return_yields_single_page():
    @command_handler(domain="order", state=OrderState)
    class OrderAggregate:
        @handles(CreateOrder)
        def on_create(self, cmd, state, seq):
            return OrderCreated(order_id=cmd.order_id)

    router = Router("agg").with_handler(OrderAggregate, lambda: OrderAggregate()).build()
    response = router.dispatch(_make_contextual_command(CreateOrder(order_id="o-1")))

    assert response.HasField("events")
    assert len(response.events.pages) == 1
    assert response.events.pages[0].header.sequence == 0  # default seq


def test_event_carries_incremented_sequence_from_seq_parameter():
    """Handler receives seq and can set it on returned pages implicitly.

    The dispatcher sets page.header.sequence = seq_passed_to_handler for the
    first emitted event, advancing by 1 per subsequent event.
    """

    @command_handler(domain="order", state=OrderState)
    class OrderAggregate:
        @handles(CreateOrder)
        def on_create(self, cmd, state, seq):
            return OrderCreated(order_id=cmd.order_id)

    router = Router("agg").with_handler(OrderAggregate, lambda: OrderAggregate()).build()
    prior = EventBook()
    prior.next_sequence = 7
    response = router.dispatch(
        _make_contextual_command(CreateOrder(order_id="o-1"), prior=prior)
    )

    assert response.events.pages[0].header.sequence == 7
