"""Step defs for features/client/rejected_compensation.feature.

Extends rejection.feature coverage with state-rebuild-before-handler,
multi-method routing, sequence stamping on compensation events, and
empty-handler behavior. Uses the real Python router dispatch path —
@command_handler + @rejected + @applies + Router.build() — matching
the Rust-side approach in tests/steps/rejected_compensation.rs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pytest_bdd import given, parsers, scenarios, then, when

from angzarr_client.router import (
    Router,
    applies,
    command_handler,
    rejected,
)
from tests.client.steps._helpers import contextual_notification, notification_for
from tests.fixtures import (
    CreateShipment,
    FundsDeposited,
    FundsReleased,
    ProcessPayment,
    ReserveStock,
    WorkflowFailed,
)

scenarios("rejected_compensation.feature")


@dataclass
class _PaymentState:
    bankroll: int = 0


# --- Given — variant markers ------------------------------------------------


@given('a command handler "Payment" for domain "payment" with stateful rejection')
def _given_stateful(world):
    world.classes["__variant__"] = "stateful"
    world.classes["__applies__"] = False


@given('a command handler "Payment" for domain "payment" with two @rejected handlers')
def _given_two_rejected(world):
    world.classes["__variant__"] = "double"


@given('a command handler "Payment" for domain "payment" with no rejection handlers')
def _given_no_rejected(world):
    @command_handler(domain="payment", state=_PaymentState)
    class Payment:
        pass

    world.classes["Payment"] = Payment


@given("Payment @applies FundsDeposited by setting state.bankroll")
def _payment_applies(world):
    world.classes["__applies__"] = True


@given(
    'Payment has a @rejected("inventory", "ReserveStock") handler that emits '
    "FundsReleased carrying state.bankroll"
)
def _build_stateful_funds(world):
    applies_on = world.classes.get("__applies__", False)

    @command_handler(domain="payment", state=_PaymentState)
    class Payment:
        if applies_on:

            @applies(FundsDeposited)
            def apply_dep(self, state, event):
                state.bankroll = event.new_bankroll

        @rejected("inventory", "ReserveStock")
        def on_reserve_rejected(self, _notif, state):
            return FundsReleased(amount=state.bankroll, reason="compensation")

    world.classes["Payment"] = Payment


@given(
    'Payment has a @rejected("inventory", "ReserveStock") handler emitting '
    "two FundsReleased events"
)
def _build_two_events(world):
    @command_handler(domain="payment", state=_PaymentState)
    class Payment:
        @rejected("inventory", "ReserveStock")
        def on_reserve_rejected(self, _notif, _state):
            return [
                FundsReleased(amount=10, reason="first"),
                FundsReleased(amount=20, reason="second"),
            ]

    world.classes["Payment"] = Payment


@given(
    'Payment has a @rejected("inventory", "ReserveStock") handler emitting '
    "FundsReleased"
)
def _add_funds_released(world):
    world.classes.setdefault("__pending__", {})["funds_released"] = True
    _maybe_build_double(world)


@given(
    'Payment has a @rejected("payment", "ProcessPayment") handler emitting '
    "WorkflowFailed"
)
def _add_workflow_failed(world):
    world.classes.setdefault("__pending__", {})["workflow_failed"] = True
    _maybe_build_double(world)


def _maybe_build_double(world):
    pending = world.classes.get("__pending__", {})
    if pending.get("funds_released") and pending.get("workflow_failed"):

        @command_handler(domain="payment", state=_PaymentState)
        class Payment:
            @rejected("inventory", "ReserveStock")
            def on_reserve(self, _notif, _state):
                return FundsReleased(amount=1, reason="stock-failed")

            @rejected("payment", "ProcessPayment")
            def on_payment(self, _notif, _state):
                return WorkflowFailed(
                    reason="payment rejected",
                    failed_domain="payment",
                    failed_command="ProcessPayment",
                )

        world.classes["Payment"] = Payment


@given("the router is built with the Payment handler")
def _given_router_built(world):
    cls = world.classes["Payment"]
    inst = cls()
    world.handlers.append(inst)
    world.router = Router("agg").with_handler(type(inst), lambda i=inst: i).build()


# --- Given — prior EventBook ------------------------------------------------


@given(
    parsers.parse(
        "a prior EventBook with a FundsDeposited event of bankroll {amount:d}"
    )
)
def _given_prior_deposited(world, amount: int):
    from tests.client.steps._helpers import event_book

    world.prior_events = event_book(
        [FundsDeposited(new_bankroll=amount)], domain="payment"
    )


@given(parsers.parse("a prior EventBook whose next_sequence is {seq:d}"))
def _given_prior_next_seq(world, seq: int):
    from tests.client.steps._helpers import event_book

    book = event_book([], domain="payment")
    book.next_sequence = seq
    world.prior_events = book


# --- When -------------------------------------------------------------------


@when(
    parsers.parse(
        'a Notification wrapping a rejected {cmd_kind} in domain "{target}" '
        "is dispatched"
    )
)
def _when_notification_dispatched(world, cmd_kind: str, target: str):
    ctors = {
        "ReserveStock": lambda: ReserveStock(order_id="o-1", sku="sku-1", quantity=1),
        "ProcessPayment": lambda: ProcessPayment(order_id="o-1"),
        "CreateShipment": lambda: CreateShipment(order_id="o-1", address="addr"),
    }
    cmd = ctors[cmd_kind]()
    notif = notification_for(cmd, target_domain=target)
    req = contextual_notification(notif, aggregate_domain="payment")
    if world.prior_events is not None:
        req.events.CopyFrom(world.prior_events)
    world.response = world.router.dispatch(req)


# --- Then -------------------------------------------------------------------


def _pages(world):
    return list(world.response.events.pages)


def _type_url(page) -> str:
    return page.event.type_url


@then("the response contains one FundsReleased event")
def _then_one_funds_released(world):
    pages = _pages(world)
    assert len(pages) == 1
    assert _type_url(pages[0]).endswith("FundsReleased")


@then("the response contains one WorkflowFailed event")
def _then_one_workflow_failed(world):
    pages = _pages(world)
    assert len(pages) == 1
    assert _type_url(pages[0]).endswith("WorkflowFailed")


@then("the response contains no events")
def _then_no_events(world):
    assert len(_pages(world)) == 0


@then(parsers.parse("the FundsReleased event carries amount {amount:d}"))
def _then_amount(world, amount: int):
    pages = _pages(world)
    assert pages
    fr = FundsReleased()
    fr.ParseFromString(pages[0].event.value)
    assert fr.amount == amount, f"expected {amount}, got {fr.amount}"


@then("no FundsReleased event is emitted")
def _then_no_funds(world):
    for p in _pages(world):
        assert not _type_url(p).endswith("FundsReleased")


@then(parsers.parse("the emitted pages carry sequences [{seq_list}]"))
def _then_sequences(world, seq_list: str):
    expected = [int(s.strip()) for s in seq_list.split(",")]
    actual = [int(p.header.sequence) for p in _pages(world)]
    assert actual == expected, f"expected {expected}, got {actual}"
