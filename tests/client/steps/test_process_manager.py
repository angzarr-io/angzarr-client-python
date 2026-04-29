"""Step defs for features/process_manager.feature."""

from __future__ import annotations

from dataclasses import dataclass

from pytest_bdd import given, parsers, scenarios, then, when

from angzarr_client.proto.angzarr import EventBook
from angzarr_client.router import (
    ProcessManagerResponse,
    Router,
    applies,
    handles,
    process_manager,
)
from tests.client.steps._helpers import command_book, pm_request
from tests.fixtures import OrderCompleted, OrderCreated, ReserveStock, StockReserved

scenarios("process_manager.feature")


@dataclass
class WorkflowState:
    orders_seen: int = 0


@given(parsers.parse('a process manager "Fulfillment" with pm_domain "fulfillment"'))
def _given_pm_stub(world):
    world.classes["__pm_config__"] = {"pm_domain": "fulfillment"}


@given(parsers.parse('the PM sources from "{a}" and "{b}"'))
def _given_pm_sources(world, a, b):
    world.classes["__pm_config__"]["sources"] = [a, b]


@given(parsers.parse('the PM targets "{t}"'))
def _given_pm_targets(world, t):
    world.classes["__pm_config__"]["targets"] = [t]


@given("the PM has state WorkflowState with orders_seen int")
def _given_pm_state(world):
    world.classes["__pm_config__"]["state"] = WorkflowState


@given("the PM applies OrderCompleted by incrementing state.orders_seen")
def _given_pm_applier(world):
    world.classes["__pm_applier__"] = True


@given("the PM handles OrderCreated by emitting a ReserveStock command")
def _given_pm_handler(world):
    world.classes["__pm_handler__"] = True


@given("the router is built with the Fulfillment PM")
def _given_pm_built(world):
    cfg = world.classes["__pm_config__"]
    observed = world.observed
    # Audit #86: read flags lazily inside the handler — `Given` steps
    # like "the PM also emits an OrderTracked process_event" or "the PM
    # handler sets outgoing edition X" run AFTER the Background's
    # router-built step, so capturing world state at decoration time
    # would see stale values.
    classes = world.classes

    @process_manager(
        name="pm-fulfillment",
        pm_domain=cfg["pm_domain"],
        sources=cfg["sources"],
        targets=cfg["targets"],
        state=cfg["state"],
    )
    class Fulfillment:
        @applies(OrderCompleted)
        def apply_completed(self, state, event):
            state.orders_seen += 1

        @handles(OrderCreated)
        def on_order(self, event, state, destinations):
            observed["orders_seen"] = state.orders_seen
            cb = command_book(
                ReserveStock(order_id=event.order_id, sku="x", quantity=1),
                target_domain="inventory",
            )
            outgoing_edition = classes.get("__pm_outgoing_edition__")
            if outgoing_edition:
                cb.cover.edition.name = outgoing_edition
            response = ProcessManagerResponse(commands=[cb])
            if classes.get("__pm_emit_process_event__", False):
                pe = EventBook()
                pe.cover.domain = cfg["pm_domain"]
                response.process_events = [pe]
            return response

    world.handlers.append(Fulfillment())
    h = world.handlers[0]
    world.router = Router("pms").with_handler(type(h), lambda h=h: h).build()


@when("an OrderCreated trigger is dispatched to the PM router")
def _when_pm_trigger_created(world):
    prior = world.classes.get("__pm_prior__", [])
    req = pm_request(
        [OrderCreated(order_id="o-1")],
        source_domain="order",
        process_state_msgs=prior,
    )
    # Audit #86: thread the configured trigger edition into the
    # request's trigger cover so dispatch can propagate it.
    if world.source_edition is not None:
        name, divergences = world.source_edition
        req.trigger.cover.edition.name = name
        for dom, seq in divergences:
            req.trigger.cover.edition.divergences.add(domain=dom, sequence=seq)
    world.response = world.router.dispatch(req)


@when("a StockReserved trigger with a domain outside sources is dispatched")
def _when_pm_trigger_outside(world):
    world.response = world.router.dispatch(
        pm_request(
            [StockReserved(order_id="x", sku="a", quantity=1)],
            source_domain="shipping",  # not in the PM's sources list
        )
    )


@given("process state events: OrderCompleted, OrderCompleted")
def _given_pm_prior(world):
    world.classes["__pm_prior__"] = [
        OrderCompleted(order_id="a"),
        OrderCompleted(order_id="b"),
    ]


@then(parsers.parse("the PM observed state.orders_seen = {n:d}"))
def _then_pm_orders_seen(world, n):
    assert world.observed["orders_seen"] == n


# --------------------------------------------------------------------------
# Audit #86: edition propagation step impls (C-0143..C-0145).
# --------------------------------------------------------------------------


@given(parsers.parse('the trigger event has edition "{name}"'))
def _given_trigger_edition(world, name):
    world.source_edition = (name, [])


@given("the PM also emits an OrderTracked process_event on OrderCreated")
def _given_pm_emits_process_event(world):
    """Audit #86 C-0144: flag picked up by `_given_pm_built` so the
    handler also returns a `process_events` EventBook."""
    world.classes["__pm_emit_process_event__"] = True


@given(parsers.parse('the PM handler sets outgoing edition "{outgoing}"'))
def _given_pm_handler_sets_outgoing_edition(world, outgoing):
    """Audit #86 C-0145: flag picked up by `_given_pm_built` so the
    handler sets a non-empty edition on the outgoing command. The
    framework must overwrite it with the trigger edition."""
    world.classes["__pm_outgoing_edition__"] = outgoing


@then(parsers.parse('the emitted command\'s cover has edition "{expected}"'))
def _then_pm_command_edition(world, expected):
    cover = world.response.commands[0].cover
    actual = cover.edition.name if cover.HasField("edition") else ""
    assert actual == expected


@then(parsers.parse('the emitted process_event\'s cover has edition "{expected}"'))
def _then_pm_process_event_edition(world, expected):
    assert world.response.HasField("process_events")
    cover = world.response.process_events.cover
    actual = cover.edition.name if cover.HasField("edition") else ""
    assert actual == expected
