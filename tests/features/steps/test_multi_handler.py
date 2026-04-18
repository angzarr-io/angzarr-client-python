"""Step defs for features/multi_handler.feature."""

from __future__ import annotations

from dataclasses import dataclass

from pytest_bdd import given, parsers, scenarios, then, when

from angzarr_client.router import Router, applies, command_handler, handles
from tests.features.steps._helpers import contextual_command, event_book
from tests.fixtures import CreateOrder, OrderCompleted, OrderCreated

scenarios("multi_handler.feature")


@dataclass
class StateA:
    counter_a: int = 0


@dataclass
class StateB:
    counter_b: int = 0


@given(parsers.parse('two command handlers Alpha and Beta for domain "order"'))
def _given_alpha_beta(world):
    world.classes["Alpha"] = None  # placeholders filled by later @given steps
    world.classes["Beta"] = None
    world.classes["__alpha_emits_n__"] = 1
    world.classes["__beta_emits_n__"] = 1
    world.classes["__alpha_applier__"] = None
    world.classes["__beta_applier__"] = None


@given("Alpha handles CreateOrder by emitting OrderCreated")
def _given_alpha_handles_create_order(world):
    world.classes["__alpha_handler__"] = "OrderCreated"


@given("Beta handles CreateOrder by emitting OrderCompleted")
def _given_beta_handles_create_order(world):
    world.classes["__beta_handler__"] = "OrderCompleted"


def _build_alpha_beta(world):
    """Deferred Alpha/Beta construction once all configuration is in place."""
    call_log = world.call_log
    observed = world.observed

    alpha_emits = world.classes.get("__alpha_emits_n__", 1)
    beta_emits = world.classes.get("__beta_emits_n__", 1)
    alpha_applier = world.classes.get("__alpha_applier__")
    beta_applier = world.classes.get("__beta_applier__")

    @command_handler(domain="order", state=StateA)
    class Alpha:
        if alpha_applier is not None:

            @applies(alpha_applier)
            def apply(self, state, event):
                state.counter_a += 1

        @handles(CreateOrder)
        def on(self, cmd, state, seq):
            call_log.append("Alpha")
            observed["alpha_seq"] = seq
            observed["alpha_state"] = state.counter_a
            events = [OrderCreated(order_id=cmd.order_id) for _ in range(alpha_emits)]
            return events[0] if len(events) == 1 else tuple(events)

    @command_handler(domain="order", state=StateB)
    class Beta:
        if beta_applier is not None:

            @applies(beta_applier)
            def apply(self, state, event):
                state.counter_b += 1

        @handles(CreateOrder)
        def on(self, cmd, state, seq):
            call_log.append("Beta")
            observed["beta_seq"] = seq
            observed["beta_state"] = state.counter_b
            events = [OrderCompleted(order_id=cmd.order_id) for _ in range(beta_emits)]
            return events[0] if len(events) == 1 else tuple(events)

    world.classes["Alpha"] = Alpha
    world.classes["Beta"] = Beta
    world.handlers = [Alpha(), Beta()]
    world.router = (
        Router("agg")
        .with_handler(type(world.handlers[0]), lambda i=0: world.handlers[i])
        .with_handler(type(world.handlers[1]), lambda i=1: world.handlers[i])
        .build()
    )


@given("the router is built with Alpha then Beta")
def _given_built_alpha_beta(world):
    _build_alpha_beta(world)


# --- Scenario 1: both called, merged order ---


@when(parsers.parse('CreateOrder(order_id="{order_id}") is dispatched'))
def _when_create_order(world, order_id):
    world.response = world.router.dispatch(
        contextual_command(
            CreateOrder(order_id=order_id), domain="order", prior=world.prior_events
        )
    )


@then("Alpha was called before Beta")
def _then_call_order(world):
    idx_a = world.call_log.index("Alpha")
    idx_b = world.call_log.index("Beta")
    assert idx_a < idx_b


@then("the response contains two events in [OrderCreated, OrderCompleted] order")
def _then_events_ordered(world):
    pages = world.response.events.pages
    assert len(pages) == 2
    assert pages[0].event.type_url.endswith("OrderCreated")
    assert pages[1].event.type_url.endswith("OrderCompleted")


# --- Scenario 2: seq increments across handlers ---


@given(parsers.parse("the prior EventBook's next_sequence is {n:d}"))
def _given_next_seq(world, n):
    world.prior_events = event_book([], domain="order")
    world.prior_events.next_sequence = n


@given(parsers.parse("Alpha emits two events per call"))
def _given_alpha_two(world):
    world.classes["__alpha_emits_n__"] = 2


@given(parsers.parse("Beta emits one event per call"))
def _given_beta_one(world):
    world.classes["__beta_emits_n__"] = 1
    # Configuration is now complete — build the router.
    _build_alpha_beta(world)


@when("CreateOrder is dispatched")
def _when_dispatch_plain(world):
    world.response = world.router.dispatch(
        contextual_command(
            CreateOrder(order_id="o-1"), domain="order", prior=world.prior_events
        )
    )


@then(parsers.parse("Alpha observed seq = {n:d}"))
def _then_alpha_seq(world, n):
    assert world.observed["alpha_seq"] == n


@then(parsers.parse("Beta observed seq = {n:d}"))
def _then_beta_seq(world, n):
    assert world.observed["beta_seq"] == n


@then("the emitted pages carry sequences [5, 6, 7]")
def _then_merged_seqs(world):
    seqs = [p.header.sequence for p in world.response.events.pages]
    assert seqs == [5, 6, 7]


# --- Scenario 3: state isolation ---


@given("Alpha applies OrderCreated by incrementing counter_a")
def _given_alpha_applier(world):
    world.classes["__alpha_applier__"] = OrderCreated


@given("Beta applies OrderCompleted by incrementing counter_b")
def _given_beta_applier(world):
    world.classes["__beta_applier__"] = OrderCompleted


@given("a prior EventBook with [OrderCreated, OrderCreated, OrderCompleted]")
def _given_prior_mix(world):
    world.prior_events = event_book(
        [
            OrderCreated(order_id="a"),
            OrderCreated(order_id="b"),
            OrderCompleted(order_id="a"),
        ],
        domain="order",
    )
    # Build the router now that all gived-configuration is known.
    _build_alpha_beta(world)


@when("a command is dispatched")
def _when_dispatch_any(world):
    world.response = world.router.dispatch(
        contextual_command(
            CreateOrder(order_id="o-1"), domain="order", prior=world.prior_events
        )
    )


@then(parsers.parse("Alpha observed counter_a = {n:d}"))
def _then_alpha_counter(world, n):
    assert world.observed["alpha_state"] == n


@then(parsers.parse("Beta observed counter_b = {n:d}"))
def _then_beta_counter(world, n):
    assert world.observed["beta_state"] == n
