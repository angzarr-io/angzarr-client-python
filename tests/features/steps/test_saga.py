"""Step defs for features/saga.feature."""

from __future__ import annotations

from pytest_bdd import given, parsers, scenarios, then, when

from angzarr_client.router import Router, handles, saga
from tests.features.steps._helpers import command_book, saga_request
from tests.fixtures import OrderCreated, ReserveStock, StockReserved

scenarios("saga.feature")


@given(
    parsers.parse('a saga "OrderFulfillment" translating from "order" to "inventory"')
)
def _given_saga_class(world):
    @saga(name="saga-order-fulfillment", source="order", target="inventory")
    class OrderFulfillment:
        pass

    world.classes["OrderFulfillment"] = OrderFulfillment


@given("the saga handles OrderCreated by emitting a ReserveStock command")
def _given_saga_handler(world):
    cls = world.classes["OrderFulfillment"]
    observed = world.observed_dest

    @handles(OrderCreated)
    def translate(self, event, destinations):
        observed["inventory"] = destinations.sequence_for("inventory")
        observed["fulfillment"] = destinations.sequence_for("fulfillment")
        return command_book(
            ReserveStock(order_id=event.order_id, sku="sku-1", quantity=1),
            target_domain="inventory",
        )

    cls.translate = translate


@given("the router is built with the OrderFulfillment saga")
def _given_saga_built(world):
    world.handlers.append(world.classes["OrderFulfillment"]())
    world.router = Router("sagas").with_handler(type(world.handlers[0]), lambda i=0: world.handlers[i]).build()


@when("an OrderCreated event is dispatched to the saga router")
def _when_saga_dispatch_ordercreated(world):
    world.response = world.router.dispatch(
        saga_request(
            [OrderCreated(order_id="o-1", customer_id="c-1")],
            source_domain="order",
            dest_seqs=world.dest_seqs or None,
        )
    )


@when("a StockReserved event is dispatched to the saga router")
def _when_saga_dispatch_stockreserved(world):
    world.response = world.router.dispatch(
        saga_request(
            [StockReserved(order_id="o-1", sku="sku-1", quantity=1)],
            source_domain="order",
        )
    )


@then("the response contains exactly one command")
def _then_one_command(world):
    assert len(world.response.commands) == 1


@then(parsers.parse('the command targets the "{domain}" domain'))
def _then_command_targets(world, domain):
    assert world.response.commands[0].cover.domain == domain


@then("the response contains no commands")
def _then_no_commands(world):
    assert len(world.response.commands) == 0


@given(parsers.parse("destination sequences inventory={inv:d} and fulfillment={ful:d}"))
def _given_dest_seqs(world, inv, ful):
    world.dest_seqs = {"inventory": inv, "fulfillment": ful}


@then(parsers.parse("the saga observed destination inventory = {n:d}"))
def _then_saga_inv(world, n):
    assert world.observed_dest["inventory"] == n


@then(parsers.parse("the saga observed destination fulfillment = {n:d}"))
def _then_saga_ful(world, n):
    assert world.observed_dest["fulfillment"] == n
