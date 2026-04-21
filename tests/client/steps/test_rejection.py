"""Step defs for features/rejection.feature."""

from __future__ import annotations

from dataclasses import dataclass

from pytest_bdd import given, parsers, scenarios, then, when

from angzarr_client.router import Router, command_handler, rejected
from tests.client.steps._helpers import contextual_notification, notification_for
from tests.fixtures import FundsReleased, ProcessPayment, ReserveStock

scenarios("rejection.feature")


@dataclass
class PaymentState:
    pass


@given(
    parsers.parse(
        'a command handler "Payment" for domain "payment" with state PaymentState'
    )
)
def _given_payment_handler(world):
    world.classes["__payment_handlers__"] = []


@given(
    parsers.parse(
        'Payment has a @rejected("{src_domain}", "{cmd}") handler emitting FundsReleased'
    )
)
def _given_rejected(world, src_domain, cmd):
    call_log = world.call_log

    @command_handler(domain="payment", state=PaymentState)
    class Payment:
        @rejected(src_domain, cmd)
        def on_reserve_rejected(self, notif, state):
            call_log.append("Payment")
            return FundsReleased(amount=100, reason="reserve-failed")

    world.classes.setdefault("__payment_handlers__", []).append(Payment)


@given(
    parsers.parse(
        "a second Payment handler Payment2 with the same @rejected key emitting FundsReleased"
    )
)
def _given_payment2(world):
    call_log = world.call_log

    @command_handler(domain="payment", state=PaymentState)
    class Payment2:
        @rejected("inventory", "ReserveStock")
        def on_reserve_rejected(self, notif, state):
            call_log.append("Payment2")
            return FundsReleased(amount=200, reason="double-check")

    world.classes["__payment_handlers__"].append(Payment2)


@given("the router is built with the Payment handler")
def _given_built_single(world):
    handler_cls = world.classes["__payment_handlers__"][0]
    world.handlers.append(handler_cls())
    h = world.handlers[0]
    world.router = Router("agg").with_handler(type(h), lambda h=h: h).build()


@given("the router is built with Payment then Payment2")
def _given_built_both(world):
    classes = world.classes["__payment_handlers__"]
    assert len(classes) >= 2
    world.handlers = [classes[0](), classes[1]()]
    h0, h1 = world.handlers[0], world.handlers[1]
    world.router = (
        Router("agg")
        .with_handler(type(h0), lambda h=h0: h)
        .with_handler(type(h1), lambda h=h1: h)
        .build()
    )


@when(
    parsers.parse(
        'a Notification wrapping a rejected {cmd_kind} in domain "{target}" is dispatched'
    )
)
def _when_rejection_dispatched(world, cmd_kind, target):
    if cmd_kind == "ReserveStock":
        cmd = ReserveStock(order_id="o-1", sku="sku-1", quantity=1)
    elif cmd_kind == "ProcessPayment":
        cmd = ProcessPayment(order_id="o-1")
    else:
        raise ValueError(f"unknown rejected cmd kind: {cmd_kind}")

    notif = notification_for(cmd, target_domain=target)
    world.response = world.router.dispatch(
        contextual_notification(notif, aggregate_domain="payment")
    )


@then("the response contains one FundsReleased event")
def _then_one_funds_released(world):
    assert world.response.HasField("events")
    assert len(world.response.events.pages) == 1
    assert world.response.events.pages[0].event.type_url.endswith("FundsReleased")


@then("the response contains no events")
def _then_no_events(world):
    assert world.response.HasField("events")
    assert len(world.response.events.pages) == 0


@then("the response contains two FundsReleased events in registration order")
def _then_two_funds_released(world):
    assert len(world.response.events.pages) == 2
    for page in world.response.events.pages:
        assert page.event.type_url.endswith("FundsReleased")
    assert world.call_log == ["Payment", "Payment2"]
