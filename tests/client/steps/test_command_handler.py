"""Step defs for features/command_handler.feature."""

from __future__ import annotations

from dataclasses import dataclass

import grpc
from pytest_bdd import given, parsers, scenarios, then, when

from google.protobuf.any_pb2 import Any as ProtoAny

from angzarr_client.helpers import TYPE_URL_PREFIX
from angzarr_client.proto.angzarr.v1.types_pb2 import Cover, PageHeader
from angzarr_client.router import (
    Router,
    command_handler,
    applies,
    handles,
    state_factory,
)
from tests.client.steps._helpers import contextual_command, event_book
from tests.fixtures import CompleteOrder, CreateOrder, OrderCreated

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


@given(
    parsers.parse('a command handler "{name}" for domain "{domain}" with order state')
)
def _given_handler_with_order_state(world, name, domain):
    @command_handler(domain=domain, state=OrderState)
    class Handler:
        pass

    world.classes[name] = Handler


@given("the handler applies OrderCreated by setting state.created = true")
@given("Order applies OrderCreated by setting state.created = true")
@given("OrderCreated marks the order as created")
def _given_applier(world):
    cls = world.classes["Order"]

    @applies(OrderCreated)
    def apply_created(self, state, event):
        state.created = True

    cls.apply_created = apply_created


@given("the handler handles CreateOrder by emitting OrderCreated")
@given("CreateOrder emits OrderCreated")
def _given_handler(world):
    cls = world.classes["Order"]

    @handles(CreateOrder)
    def on_create(self, cmd, state, seq):
        return OrderCreated(order_id=cmd.order_id, customer_id="c-1")

    cls.on_create = on_create


@given("the router is built with the Order handler")
@given("Order is the active aggregate handler")
def _given_built(world):
    world.handlers.append(world.classes["Order"]())
    h = world.handlers[0]
    world.router = Router("agg").with_handler(type(h), lambda h=h: h).build()


@when(parsers.parse('CreateOrder(order_id="{order_id}") is dispatched'))
def _when_dispatch_create(world, order_id):
    cmd = contextual_command(CreateOrder(order_id=order_id), domain="order")
    incoming_ext = world.observed.get("incoming_ext")
    if incoming_ext is not None:
        cmd.command.cover.ext.CopyFrom(incoming_ext)
    try:
        world.response = world.router.dispatch(cmd)
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


@given(parsers.parse("a prior history with an OrderCreated event at sequence {seq:d}"))
def _given_prior_history_at_seq(world, seq):
    book = event_book([OrderCreated(order_id="o-1", customer_id="c-x")], domain="order")
    book.pages[0].header.CopyFrom(PageHeader(sequence=seq))
    book.next_sequence = seq + 1
    world.prior_events = book


@given("no prior events in the incoming ContextualCommand")
def _given_no_prior(world):
    world.prior_events = event_book([], domain="order")


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
@then("the order is treated as already created")
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
@then("the unknown command is rejected as invalid input")
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
@then("when the handler emits nothing, no events are produced")
def _then_no_pages(world):
    assert world.response.HasField("events")
    assert len(world.response.events.pages) == 0


# ---- @state_factory override ----


@given("Order has a @state_factory method returning OrderState(created=True)")
@given("the aggregate supplies its own initial state with created = true")
def _given_state_factory_override(world):
    @command_handler(domain="order", state=OrderState)
    class Order:
        @state_factory
        def make_state(self):
            return OrderState(created=True)

    world.classes["Order"] = Order


@given("Order has no @state_factory method")
@given("the aggregate does not supply its own initial state")
def _given_no_state_factory(world):
    @command_handler(domain="order", state=OrderState)
    class Order:
        pass

    world.classes["Order"] = Order


@given(
    "Order handles CreateOrder by emitting OrderCreated only when state.created is True"
)
@given(
    "Order handles CreateOrder by emitting OrderCreated only when "
    "the order is already created"
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
@given("Order handles CreateOrder by reading whether the order is created")
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
@then("the handler observes that the order is not created")
def _then_state_default_false(world):
    assert world.observed["created"] is False


# ---------------------------------------------------------------------------
# Cover.ext propagation (C-0146, C-0148). C-0147 (handler-set ext wins) is
# not exercisable through the public dispatch path today — handlers return
# events, not a cover, so there's no API to set EventBook.cover.ext from a
# handler. The fill-only semantic is unit-tested directly against
# _propagate_cover_ext in tests/router/test_dispatch_command_handler.py.
# ---------------------------------------------------------------------------


@given("the incoming command has cover.ext set to a packed parent Cover")
def _given_incoming_ext_packed(world):
    parent = Cover(domain="tournament")
    ext = ProtoAny()
    ext.Pack(parent, type_url_prefix=TYPE_URL_PREFIX)
    world.observed["incoming_ext"] = ext


@given("the incoming command's cover has no ext field set")
def _given_incoming_no_ext(world):
    world.observed["incoming_ext"] = None


@then("the response's EventBook cover.ext is the same packed parent Cover")
def _then_book_ext_same_packed(world):
    expected = world.observed["incoming_ext"]
    actual = world.response.events.cover.ext
    assert actual.type_url == expected.type_url
    assert actual.value == expected.value


@then("the response's EventBook cover has no ext field set")
def _then_book_no_ext(world):
    assert not world.response.events.cover.HasField("ext")


# C-0147: stubs remain — see file header. Removing these would silently
# break decorator-discovery for the scenario. Scenario stays in
# WIP_SCENARIOS until the router exposes a handler-cover surface.
@given("a command handler whose emit step sets EventBook cover.ext explicitly")
def _given_handler_sets_ext(world):
    raise NotImplementedError(
        "C-0147 framework gap: handlers can't set EventBook.cover.ext "
        "through the current router/dispatch surface."
    )


@given("the incoming command also has a different cover.ext set")
def _given_incoming_different_ext(world):
    raise NotImplementedError(
        "C-0147 framework gap: paired with _given_handler_sets_ext."
    )


@then("the response's EventBook cover.ext is the handler-set value")
def _then_book_ext_handler_set(world):
    raise NotImplementedError(
        "C-0147 framework gap: assertion unreachable until handler-cover "
        "surface exists."
    )
