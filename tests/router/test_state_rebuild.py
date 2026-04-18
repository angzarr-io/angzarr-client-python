"""R7: state rebuild via @applies before handler invocation.

Before ``@handles`` fires, the dispatcher replays the ``ContextualCommand.events``
(prior EventBook) through the instance's ``@applies`` methods, mutating a fresh
state instance. The handler then receives the rebuilt state.

A ``@state_factory`` method, if present, overrides the default ``state()``
constructor for producing the initial state.

State is per-instance; multiple instances rebuild independently (tested later
in R8). Appliers with no matching event type are skipped.
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
    state_factory,
)
from tests.fixtures import (
    CreateOrder,
    OrderCompleted,
    OrderCreated,
)


@dataclass
class OrderState:
    created: bool = False
    completed: bool = False
    last_customer: str = ""


def _event_page(msg, seq: int) -> EventPage:
    page = EventPage()
    page.header.CopyFrom(PageHeader(sequence=seq))
    any_msg = ProtoAny()
    any_msg.type_url = TYPE_URL_PREFIX + msg.DESCRIPTOR.full_name
    any_msg.value = msg.SerializeToString()
    page.event.CopyFrom(any_msg)
    return page


def _contextual_command(
    cmd, prior_events: list, domain: str = "order"
) -> ContextualCommand:
    """Wrap cmd + a list of prior event messages into a ContextualCommand."""
    book = EventBook()
    book.cover.CopyFrom(Cover(domain=domain))
    for offset, evt in enumerate(prior_events):
        book.pages.append(_event_page(evt, seq=offset))
    book.next_sequence = len(prior_events)

    any_cmd = ProtoAny()
    any_cmd.type_url = TYPE_URL_PREFIX + cmd.DESCRIPTOR.full_name
    any_cmd.value = cmd.SerializeToString()

    page = CommandPage()
    page.header.CopyFrom(PageHeader(sequence=len(prior_events)))
    page.command.CopyFrom(any_cmd)

    cbook = CommandBook()
    cbook.cover.CopyFrom(Cover(domain=domain))
    cbook.pages.append(page)

    request = ContextualCommand()
    request.command.CopyFrom(cbook)
    request.events.CopyFrom(book)
    return request


# --------------------------------------------------------------------------
# Appliers replay prior events into state
# --------------------------------------------------------------------------


def test_applier_mutates_state_from_prior_event():
    captured = {}

    @command_handler(domain="order", state=OrderState)
    class Aggregate:
        @applies(OrderCreated)
        def apply_created(self, state, event):
            state.created = True
            state.last_customer = event.customer_id

        @handles(OrderCompleted)
        def on_complete(self, cmd, state, seq):
            captured["state"] = state
            return None  # no emitted events

    router = Router("agg").with_handler(Aggregate, lambda: Aggregate()).build()

    prior = [OrderCreated(order_id="o-1", customer_id="c-9")]
    router.dispatch(_contextual_command(OrderCompleted(order_id="o-1"), prior))

    assert captured["state"].created is True
    assert captured["state"].last_customer == "c-9"


def test_multiple_appliers_run_in_event_order():
    captured = {}

    @command_handler(domain="order", state=OrderState)
    class Aggregate:
        @applies(OrderCreated)
        def apply_created(self, state, event):
            state.created = True

        @applies(OrderCompleted)
        def apply_completed(self, state, event):
            # This runs only if a prior OrderCompleted event is in the book;
            # should still see state.created == True from the earlier apply.
            state.completed = True

        @handles(CreateOrder)
        def on_create(self, cmd, state, seq):
            captured["state"] = state
            return None

    router = Router("agg").with_handler(Aggregate, lambda: Aggregate()).build()
    prior = [OrderCreated(order_id="o-1"), OrderCompleted(order_id="o-1")]
    router.dispatch(_contextual_command(CreateOrder(order_id="o-1"), prior))

    assert captured["state"].created is True
    assert captured["state"].completed is True


def test_unknown_event_types_are_skipped():
    """Events with no matching @applies are silently skipped (not errors)."""
    captured = {}

    @command_handler(domain="order", state=OrderState)
    class Aggregate:
        @applies(OrderCreated)
        def apply_created(self, state, event):
            state.created = True

        @handles(CreateOrder)
        def on_create(self, cmd, state, seq):
            captured["state"] = state
            return None

    router = Router("agg").with_handler(Aggregate, lambda: Aggregate()).build()
    # Include an OrderCompleted event with no applier → should be skipped.
    prior = [OrderCreated(order_id="o-1"), OrderCompleted(order_id="o-1")]
    router.dispatch(_contextual_command(CreateOrder(order_id="o-1"), prior))

    assert captured["state"].created is True


def test_no_prior_events_yields_default_state():
    captured = {}

    @command_handler(domain="order", state=OrderState)
    class Aggregate:
        @applies(OrderCreated)
        def apply_created(self, state, event):
            state.created = True

        @handles(CreateOrder)
        def on_create(self, cmd, state, seq):
            captured["state"] = state
            return None

    router = Router("agg").with_handler(Aggregate, lambda: Aggregate()).build()
    router.dispatch(_contextual_command(CreateOrder(order_id="o-1"), prior_events=[]))

    # Nothing applied → default dataclass values.
    assert captured["state"].created is False
    assert captured["state"].last_customer == ""


# --------------------------------------------------------------------------
# @state_factory override
# --------------------------------------------------------------------------


def test_state_factory_overrides_default_constructor():
    captured = {}

    @command_handler(domain="order", state=OrderState)
    class Aggregate:
        @state_factory
        def seeded(self):
            return OrderState(last_customer="default-customer")

        @handles(CreateOrder)
        def on_create(self, cmd, state, seq):
            captured["state"] = state
            return None

    router = Router("agg").with_handler(Aggregate, lambda: Aggregate()).build()
    router.dispatch(_contextual_command(CreateOrder(order_id="o-1"), prior_events=[]))

    assert captured["state"].last_customer == "default-customer"


def test_state_factory_runs_before_appliers():
    captured = {}

    @command_handler(domain="order", state=OrderState)
    class Aggregate:
        @state_factory
        def seeded(self):
            return OrderState(last_customer="seed")

        @applies(OrderCreated)
        def apply_created(self, state, event):
            # Should see the seeded value first
            captured["before_apply"] = state.last_customer
            state.last_customer = event.customer_id

        @handles(CreateOrder)
        def on_create(self, cmd, state, seq):
            captured["final"] = state.last_customer
            return None

    router = Router("agg").with_handler(Aggregate, lambda: Aggregate()).build()
    prior = [OrderCreated(order_id="o-1", customer_id="from-event")]
    router.dispatch(_contextual_command(CreateOrder(order_id="o-1"), prior))

    assert captured["before_apply"] == "seed"
    assert captured["final"] == "from-event"


# --------------------------------------------------------------------------
# State isolation: rebuild produces a fresh instance each dispatch
# --------------------------------------------------------------------------


def test_state_is_isolated_between_dispatches():
    states = []

    @command_handler(domain="order", state=OrderState)
    class Aggregate:
        @applies(OrderCreated)
        def apply_created(self, state, event):
            state.created = True

        @handles(CreateOrder)
        def on_create(self, cmd, state, seq):
            states.append(state)
            return None

    router = Router("agg").with_handler(Aggregate, lambda: Aggregate()).build()
    router.dispatch(_contextual_command(CreateOrder(order_id="a"), []))
    router.dispatch(
        _contextual_command(CreateOrder(order_id="b"), [OrderCreated(order_id="a")])
    )

    # Two distinct state instances were rebuilt.
    assert states[0] is not states[1]
    assert states[0].created is False
    assert states[1].created is True
