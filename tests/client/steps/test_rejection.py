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


# ---------------------------------------------------------------------------
# WIP — Batch 6/7 new business-vocab phrasing
# Stubs raise NotImplementedError so unimplemented vocabulary fails loudly
# rather than passing silently. Replace each as the proper impl lands.
# ---------------------------------------------------------------------------


# TODO (WIP): Implement this step matcher properly.
@given(parsers.parse('Payment is a component in domain "{domain}"'))
def _given_payment_component_in_domain(world, domain):
    raise NotImplementedError("WIP: step needs implementation")


# TODO (WIP): Implement this step matcher properly.
@given("Payment compensates a rejected ReserveStock from inventory by releasing funds")
def _given_payment_compensates_releasing(world):
    raise NotImplementedError("WIP: step needs implementation")


# TODO (WIP): Implement this step matcher properly.
@given("Payment is the active component")
def _given_payment_active_component(world):
    raise NotImplementedError("WIP: step needs implementation")


# TODO (WIP): Implement this step matcher properly.
@given("a second compensation handler for the same rejection also releases funds")
def _given_second_compensator(world):
    raise NotImplementedError("WIP: step needs implementation")


# TODO (WIP): Implement this step matcher properly.
@given("Payment then Payment2 are configured")
def _given_payment_then_payment2_configured(world):
    raise NotImplementedError("WIP: step needs implementation")


# TODO (WIP): Implement this step matcher properly.
@when(parsers.parse("a rejection of {cmd_kind} arrives from {target}"))
def _when_rejection_arrives_lowercase(world, cmd_kind, target):
    raise NotImplementedError("WIP: step needs implementation")


# TODO (WIP): Implement this step matcher properly.
@then("a FundsReleased event is emitted")
def _then_a_funds_released_emitted(world):
    raise NotImplementedError("WIP: step needs implementation")


# TODO (WIP): Implement this step matcher properly.
@then("no events are emitted")
def _then_no_events_emitted(world):
    raise NotImplementedError("WIP: step needs implementation")


# TODO (WIP): Implement this step matcher properly.
@then("two FundsReleased events are emitted in registration order")
def _then_two_funds_in_order(world):
    raise NotImplementedError("WIP: step needs implementation")
