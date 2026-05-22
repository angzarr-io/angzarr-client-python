"""R10: rejection handler routing.

When a command is rejected by a downstream aggregate, the framework forwards
a Notification wrapping a RejectionNotification through the originating
component. The router dispatches the notification to the matching
``@rejected(source_domain, command)`` handler.

Key contract:
  - ``source_domain`` matches the rejected command's ``cover.domain``
  - ``command`` matches the suffix of the rejected command's proto full name
  - Multiple handlers for the same (domain, command) are all invoked,
    their compensation events merged (call-both)
  - No matching handler → empty BusinessResponse (framework delegates)
"""

from __future__ import annotations

from dataclasses import dataclass

from google.protobuf.any_pb2 import Any as ProtoAny

from angzarr_client.helpers import TYPE_URL_PREFIX
from angzarr_client.proto.angzarr.v1 import types_pb2 as types
from angzarr_client.proto.angzarr.v1.types_pb2 import (
    CommandBook,
    CommandPage,
    ContextualCommand,
    Cover,
    PageHeader,
)
from angzarr_client.router import (
    Router,
    command_handler,
    rejected,
)
from tests.fixtures import (
    FundsReleased,
    ProcessPayment,
    ReserveStock,
)


@dataclass
class PaymentState:
    refunds_issued: int = 0


def _notification_for(rejected_cmd_msg, target_domain: str) -> types.Notification:
    """Build a Notification whose RejectionNotification targets the given cmd."""
    any_cmd = ProtoAny()
    any_cmd.type_url = TYPE_URL_PREFIX + rejected_cmd_msg.DESCRIPTOR.full_name
    any_cmd.value = rejected_cmd_msg.SerializeToString()

    cpage = CommandPage()
    cpage.command.CopyFrom(any_cmd)

    cbook = CommandBook()
    cbook.cover.CopyFrom(Cover(domain=target_domain))
    cbook.pages.append(cpage)

    rej = types.RejectionNotification()
    rej.rejected_command.CopyFrom(cbook)
    rej.rejection_reason = "test rejection"

    rej_any = ProtoAny()
    rej_any.type_url = TYPE_URL_PREFIX + rej.DESCRIPTOR.full_name
    rej_any.value = rej.SerializeToString()

    n = types.Notification()
    n.payload.CopyFrom(rej_any)
    n.cover.CopyFrom(Cover(domain="payment"))
    return n


def _request_with_notification(
    notification: types.Notification, aggregate_domain: str = "payment"
) -> ContextualCommand:
    """Build a ContextualCommand whose pages[0].command is the Notification."""
    n_any = ProtoAny()
    n_any.type_url = TYPE_URL_PREFIX + notification.DESCRIPTOR.full_name
    n_any.value = notification.SerializeToString()

    cpage = CommandPage()
    cpage.header.CopyFrom(PageHeader(sequence=0))
    cpage.command.CopyFrom(n_any)

    cbook = CommandBook()
    cbook.cover.CopyFrom(Cover(domain=aggregate_domain))
    cbook.pages.append(cpage)

    req = ContextualCommand()
    req.command.CopyFrom(cbook)
    # No prior events for these tests.
    return req


# --------------------------------------------------------------------------
# Single @rejected handler invoked for matching (domain, command)
# --------------------------------------------------------------------------


def test_rejection_handler_invoked_for_matching_key():
    captured = {}

    @command_handler(domain="payment", state=PaymentState)
    class Payment:
        @rejected("inventory", "ReserveStock")
        def on_reserve_rejected(self, notif, state):
            captured["called"] = True
            return FundsReleased(amount=100, reason="reserve-failed")

    router = Router("agg").with_handler(Payment, lambda: Payment()).build()

    notif = _notification_for(ReserveStock(order_id="o-1"), target_domain="inventory")
    response = router.dispatch(_request_with_notification(notif))

    assert captured.get("called") is True
    # Compensation event carried in BusinessResponse.events
    assert response.HasField("events")
    assert len(response.events.pages) == 1
    assert response.events.pages[0].event.type_url.endswith("FundsReleased")


def test_non_matching_rejection_returns_empty_business_response():
    @command_handler(domain="payment", state=PaymentState)
    class Payment:
        @rejected("inventory", "ReserveStock")
        def on(self, notif, state):
            return FundsReleased(amount=1, reason="x")

    router = Router("agg").with_handler(Payment, lambda: Payment()).build()

    # Rejected command is ProcessPayment (different type)
    notif = _notification_for(ProcessPayment(order_id="o-1"), target_domain="inventory")
    response = router.dispatch(_request_with_notification(notif))

    # No matching handler → empty events (framework fallback)
    assert response.HasField("events")
    assert len(response.events.pages) == 0


# --------------------------------------------------------------------------
# Multi-handler merge for same (domain, command)
# --------------------------------------------------------------------------


def test_multiple_rejection_handlers_both_invoked_in_registration_order():
    call_order = []

    @command_handler(domain="payment", state=PaymentState)
    class A:
        @rejected("inventory", "ReserveStock")
        def on(self, notif, state):
            call_order.append("A")
            return FundsReleased(amount=100, reason="from-A")

    @command_handler(domain="payment", state=PaymentState)
    class B:
        @rejected("inventory", "ReserveStock")
        def on(self, notif, state):
            call_order.append("B")
            return FundsReleased(amount=200, reason="from-B")

    router = (
        Router("agg").with_handler(A, lambda: A()).with_handler(B, lambda: B()).build()
    )

    notif = _notification_for(ReserveStock(order_id="o-1"), target_domain="inventory")
    response = router.dispatch(_request_with_notification(notif))

    assert call_order == ["A", "B"]
    assert len(response.events.pages) == 2


# --------------------------------------------------------------------------
# Handler receives the Notification and state
# --------------------------------------------------------------------------


def test_rejection_handler_receives_notification_and_state():
    captured = {}

    @command_handler(domain="payment", state=PaymentState)
    class Payment:
        @rejected("inventory", "ReserveStock")
        def on(self, notif, state):
            captured["notif_type"] = type(notif).__name__
            captured["state_type"] = type(state).__name__
            return None  # No compensation

    router = Router("agg").with_handler(Payment, lambda: Payment()).build()
    notif = _notification_for(ReserveStock(order_id="o-1"), target_domain="inventory")
    router.dispatch(_request_with_notification(notif))

    assert captured["notif_type"] == "Notification"
    assert captured["state_type"] == "PaymentState"


def test_rejection_handler_returning_none_yields_no_events():
    @command_handler(domain="payment", state=PaymentState)
    class Payment:
        @rejected("inventory", "ReserveStock")
        def on(self, notif, state):
            return None

    router = Router("agg").with_handler(Payment, lambda: Payment()).build()
    notif = _notification_for(ReserveStock(order_id="o-1"), target_domain="inventory")
    response = router.dispatch(_request_with_notification(notif))

    assert response.HasField("events")
    assert len(response.events.pages) == 0


# --------------------------------------------------------------------------
# Key matching: suffix after last "." segment
# --------------------------------------------------------------------------


def test_command_name_matched_by_suffix_not_full_path():
    """A rejected command with full name "inventory.ReserveStock" matches
    @rejected("inventory", "ReserveStock") — not the full type URL."""
    called = []

    @command_handler(domain="payment", state=PaymentState)
    class Payment:
        @rejected("inventory", "ReserveStock")
        def on(self, notif, state):
            called.append(True)
            return None

    router = Router("agg").with_handler(Payment, lambda: Payment()).build()
    notif = _notification_for(ReserveStock(order_id="o-1"), target_domain="inventory")
    router.dispatch(_request_with_notification(notif))

    assert called == [True]
