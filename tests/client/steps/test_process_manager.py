"""Step defs for features/process_manager.feature."""

from __future__ import annotations

from dataclasses import dataclass

from pytest_bdd import given, parsers, scenarios, then, when

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
            return ProcessManagerResponse(
                commands=[
                    command_book(
                        ReserveStock(order_id=event.order_id, sku="x", quantity=1),
                        target_domain="inventory",
                    )
                ]
            )

    world.handlers.append(Fulfillment())
    h = world.handlers[0]
    world.router = Router("pms").with_handler(type(h), lambda h=h: h).build()


@when("an OrderCreated trigger is dispatched to the PM router")
def _when_pm_trigger_created(world):
    prior = world.classes.get("__pm_prior__", [])
    world.response = world.router.dispatch(
        pm_request(
            [OrderCreated(order_id="o-1")],
            source_domain="order",
            process_state_msgs=prior,
        )
    )


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


@then("the response contains exactly one command")
def _then_response_one_command(world):
    """Mirror Rust's `the response contains exactly one command` assertion."""
    assert world.response is not None, "expected a dispatched response"
    assert len(world.response.commands) == 1


@then("the response contains no commands")
def _then_response_no_commands(world):
    """Mirror Rust's `the response contains no commands` assertion."""
    assert world.response is not None, "expected a dispatched response"
    assert len(world.response.commands) == 0
