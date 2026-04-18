"""Step defs for features/projector.feature."""

from __future__ import annotations

from pytest_bdd import given, parsers, scenarios, then, when

from angzarr_client.router import Router, handles, projector
from tests.features.steps._helpers import event_book
from tests.fixtures import OrderCompleted, OrderCreated

scenarios("projector.feature")


@given(parsers.parse('a projector "Output" consuming domains "{dom}"'))
def _given_projector_class(world, dom):
    world.classes["__prj_domain__"] = dom


@given("the projector handles OrderCreated by appending to a write log")
def _given_projector_handler(world):
    write_log = world.write_log

    @projector(name="prj-output", domains=[world.classes["__prj_domain__"]])
    class Output:
        @handles(OrderCreated)
        def on(self, event):
            write_log.append(("OrderCreated", event.order_id))

    world.classes["Output"] = Output


@given("the router is built with the Output projector")
def _given_projector_built(world):
    world.handlers.append(world.classes["Output"]())
    world.router = Router("prjs").with_handler(type(world.handlers[0]), lambda i=0: world.handlers[i]).build()


@when("an EventBook with three OrderCreated events is dispatched")
def _when_three_created(world):
    world.router.dispatch(
        event_book(
            [
                OrderCreated(order_id="a"),
                OrderCreated(order_id="b"),
                OrderCreated(order_id="c"),
            ],
            domain="order",
        )
    )


@when("an EventBook mixing OrderCreated and OrderCompleted is dispatched")
def _when_mixed(world):
    world.router.dispatch(
        event_book(
            [
                OrderCreated(order_id="a"),
                OrderCompleted(order_id="a"),
                OrderCreated(order_id="b"),
            ],
            domain="order",
        )
    )


@when(parsers.parse('an EventBook in domain "{dom}" is dispatched'))
def _when_other_domain(world, dom):
    world.router.dispatch(event_book([OrderCreated(order_id="a")], domain=dom))


@then(parsers.parse("the write log contains {n:d} entries"))
def _then_entries_count(world, n):
    assert len(world.write_log) == n


@then("the write log contains only OrderCreated entries")
def _then_only_created(world):
    assert all(kind == "OrderCreated" for kind, _ in world.write_log)
    assert len(world.write_log) == 2


@then("the write log remains empty")
def _then_empty(world):
    assert world.write_log == []
