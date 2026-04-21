"""Step defs for features/command_handler.feature."""

from __future__ import annotations

from dataclasses import dataclass

import grpc
from pytest_bdd import given, parsers, scenarios, then, when

from angzarr_client.router import (
    Router,
    command_handler,
    applies,
    handles,
    state_factory,
)
from tests.client.steps._helpers import contextual_command, event_book
from tests.fixtures import CompleteOrder, CreateOrder, OrderCompleted, OrderCreated

scenarios("command_handler.feature")


@dataclass
class OrderState:
    created: bool = False


@given(
    parsers.parse('a command handler "Order" for domain "order" with state OrderState')
)
def _given_handler_class(world):
    @command_handler(domain="order", state=OrderState)
    class Order:
        pass

    world.classes["Order"] = Order


@given("the handler applies OrderCreated by setting state.created = true")
def _given_applier(world):
    cls = world.classes["Order"]

    @applies(OrderCreated)
    def apply_created(self, state, event):
        state.created = True

    cls.apply_created = apply_created


@given("the handler handles CreateOrder by emitting OrderCreated")
def _given_handler(world):
    cls = world.classes["Order"]

    @handles(CreateOrder)
    def on_create(self, cmd, state, seq):
        return OrderCreated(order_id=cmd.order_id, customer_id="c-1")

    cls.on_create = on_create


@given("the router is built with the Order handler")
def _given_built(world):
    world.handlers.append(world.classes["Order"]())
    h = world.handlers[0]
    world.router = Router("agg").with_handler(type(h), lambda h=h: h).build()


@when(parsers.parse('CreateOrder(order_id="{order_id}") is dispatched'))
def _when_dispatch_create(world, order_id):
    try:
        world.response = world.router.dispatch(
            contextual_command(CreateOrder(order_id=order_id), domain="order")
        )
    except grpc.RpcError as e:
        world.dispatch_exc = e


@then("the response emits an OrderCreated event")
def _then_emitted_created(world):
    assert world.response.HasField("events")
    assert len(world.response.events.pages) == 1
    assert world.response.events.pages[0].event.type_url.endswith("OrderCreated")


@then("the emitted event sequence is 0")
def _then_seq_zero(world):
    assert world.response.events.pages[0].header.sequence == 0


# ---- State rebuild scenario ----


@given("a prior EventBook with an OrderCreated event at seq 0")
def _given_prior_created(world):
    world.prior_events = event_book(
        [OrderCreated(order_id="o-1", customer_id="c-x")], domain="order"
    )


@when("a command is dispatched against the aggregate")
def _when_dispatch_against_aggregate(world):
    # Capture the rebuilt state by replacing the handler to observe it.
    observed = world.observed

    cls = world.classes["Order"]

    @handles(CompleteOrder)
    def on_complete(self, cmd, state, seq):
        observed["created"] = state.created
        return None

    cls.on_complete = on_complete
    # Rebuild router with replaced handler (same class, now also handles CompleteOrder).
    world.handlers = [cls()]
    h = world.handlers[0]
    world.router = Router("agg").with_handler(type(h), lambda h=h: h).build()

    world.response = world.router.dispatch(
        contextual_command(
            CompleteOrder(order_id="o-1"), domain="order", prior=world.prior_events
        )
    )


@then("the handler sees state.created = true")
def _then_state_rebuilt(world):
    assert world.observed["created"] is True


# ---- Unknown command scenario ----


@when(parsers.parse('CompleteOrder(order_id="{order_id}") is dispatched'))
def _when_dispatch_complete(world, order_id):
    try:
        world.response = world.router.dispatch(
            contextual_command(CompleteOrder(order_id=order_id), domain="order")
        )
    except grpc.RpcError as e:
        world.dispatch_exc = e


@then("dispatch fails with INVALID_ARGUMENT")
def _then_invalid_argument(world):
    assert world.dispatch_exc is not None
    assert world.dispatch_exc.code() == grpc.StatusCode.INVALID_ARGUMENT


# ---- None-return scenario ----


@given("a command handler whose handler returns None for CreateOrder")
def _given_none_handler(world):
    @command_handler(domain="order", state=OrderState)
    class NoneOrder:
        @handles(CreateOrder)
        def on(self, cmd, state, seq):
            return None

    world.handlers = [NoneOrder()]
    h = world.handlers[0]
    world.router = Router("agg").with_handler(type(h), lambda h=h: h).build()


@then("the response has no event pages")
def _then_no_pages(world):
    assert world.response.HasField("events")
    assert len(world.response.events.pages) == 0


# ---- @state_factory override ----


@given("Order has a @state_factory method returning OrderState(created=True)")
def _given_state_factory_override(world):
    @command_handler(domain="order", state=OrderState)
    class Order:
        @state_factory
        def make_state(self):
            return OrderState(created=True)

    world.classes["Order"] = Order


@given("Order has no @state_factory method")
def _given_no_state_factory(world):
    @command_handler(domain="order", state=OrderState)
    class Order:
        pass

    world.classes["Order"] = Order


@given(
    "Order handles CreateOrder by emitting OrderCreated only when state.created is True"
)
def _given_conditional_handler(world):
    cls = world.classes["Order"]

    @handles(CreateOrder)
    def on_create(self, cmd, state, seq):
        if not state.created:
            return None
        return OrderCreated(order_id=cmd.order_id, customer_id="c-1")

    cls.on_create = on_create
    world.handlers = [cls()]
    h = world.handlers[0]
    world.router = Router("agg").with_handler(type(h), lambda h=h: h).build()


@given("Order handles CreateOrder by reading state.created")
def _given_reading_handler(world):
    cls = world.classes["Order"]
    observed = world.observed

    @handles(CreateOrder)
    def on_create(self, cmd, state, seq):
        observed["created"] = state.created
        return None

    cls.on_create = on_create
    world.handlers = [cls()]
    h = world.handlers[0]
    world.router = Router("agg").with_handler(type(h), lambda h=h: h).build()


@then("the handler observed state.created = false")
def _then_state_default_false(world):
    assert world.observed["created"] is False
