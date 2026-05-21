"""Tests for ``Replay`` dispatch — audit #45.

Uses a real proto Message (``Cover``) as the aggregate state type so
the test exercises the actual ``Any.Pack`` / ``Any.Unpack`` round-trip
the framework relies on. Replay opt-in is via
``@command_handler(supports_replay=True)``.
"""

from __future__ import annotations

from google.protobuf.any_pb2 import Any as ProtoAny

from angzarr_client.helpers import TYPE_URL_PREFIX
from angzarr_client.proto.angzarr import Cover, EventPage, Snapshot
from angzarr_client.proto.angzarr.v1.command_handler_pb2 import ReplayRequest
from angzarr_client.router import (
    Router,
    applies,
    command_handler,
    handles,
)
from tests.fixtures import CreateOrder, OrderCreated


def _event_page(msg) -> EventPage:
    page = EventPage()
    any_msg = ProtoAny()
    any_msg.type_url = TYPE_URL_PREFIX + msg.DESCRIPTOR.full_name
    any_msg.value = msg.SerializeToString()
    page.event.CopyFrom(any_msg)
    return page


# --------------------------------------------------------------------------
# supports_replay() gate


def test_supports_replay_false_by_default():
    """Aggregates that don't pass ``supports_replay=True`` are off-by-default."""

    @command_handler(domain="order", state=Cover)
    class Order:
        @handles(CreateOrder)
        def on_create(self, cmd, state, seq):
            return []

    router = Router("orders").with_handler(Order, lambda: Order()).build()
    assert router.supports_replay() is False


def test_supports_replay_true_when_opted_in():
    @command_handler(domain="order", state=Cover, supports_replay=True)
    class Order:
        @handles(CreateOrder)
        def on_create(self, cmd, state, seq):
            return []

    router = Router("orders").with_handler(Order, lambda: Order()).build()
    assert router.supports_replay() is True


# --------------------------------------------------------------------------
# dispatch_replay


def test_dispatch_replay_starts_from_empty_state_when_no_snapshot():
    """No base_snapshot.state.value → start from default-constructed state."""

    @command_handler(domain="order", state=Cover, supports_replay=True)
    class Order:
        @applies(OrderCreated)
        def apply_created(self, state, evt):
            state.domain = "order-created-applied"

        @handles(CreateOrder)
        def on_create(self, cmd, state, seq):
            return []

    router = Router("orders").with_handler(Order, lambda: Order()).build()
    request = ReplayRequest()
    request.events.append(_event_page(OrderCreated(order_id="o-1")))
    response = router.dispatch_replay(request)

    # State should be a Cover with the applied side effect.
    out = Cover()
    response.state.Unpack(out)
    assert out.domain == "order-created-applied"


def test_dispatch_replay_uses_base_snapshot_as_starting_state():
    """``base_snapshot.state`` is decoded into the registered state type
    and serves as the starting point for applier replay."""

    @command_handler(domain="order", state=Cover, supports_replay=True)
    class Order:
        @applies(OrderCreated)
        def apply_created(self, state, evt):
            # Append (preserve existing domain).
            state.domain = state.domain + "+applied"

        @handles(CreateOrder)
        def on_create(self, cmd, state, seq):
            return []

    router = Router("orders").with_handler(Order, lambda: Order()).build()
    request = ReplayRequest()
    base = Snapshot()
    base.state.Pack(Cover(domain="initial"))
    request.base_snapshot.CopyFrom(base)
    request.events.append(_event_page(OrderCreated(order_id="o-1")))
    response = router.dispatch_replay(request)

    out = Cover()
    response.state.Unpack(out)
    assert out.domain == "initial+applied"


def test_dispatch_replay_no_events_returns_snapshot_state():
    """Empty events list → state equals base_snapshot.state."""

    @command_handler(domain="order", state=Cover, supports_replay=True)
    class Order:
        @applies(OrderCreated)
        def apply_created(self, state, evt):
            state.domain = "should-not-fire"

        @handles(CreateOrder)
        def on_create(self, cmd, state, seq):
            return []

    router = Router("orders").with_handler(Order, lambda: Order()).build()
    request = ReplayRequest()
    request.base_snapshot.state.Pack(Cover(domain="frozen"))
    response = router.dispatch_replay(request)

    out = Cover()
    response.state.Unpack(out)
    assert out.domain == "frozen"


def test_dispatch_replay_silently_skips_unknown_event_types():
    """Events without a matching ``@applies`` are skipped — replay is
    additive, not total."""

    seen = []

    @command_handler(domain="order", state=Cover, supports_replay=True)
    class Order:
        @applies(OrderCreated)
        def apply_created(self, state, evt):
            seen.append(evt.order_id)

        @handles(CreateOrder)
        def on_create(self, cmd, state, seq):
            return []

    router = Router("orders").with_handler(Order, lambda: Order()).build()
    request = ReplayRequest()
    # Mix matched + unmatched events.
    request.events.append(_event_page(OrderCreated(order_id="o-1")))
    request.events.append(_event_page(CreateOrder(order_id="ignored")))
    request.events.append(_event_page(OrderCreated(order_id="o-2")))
    router.dispatch_replay(request)

    assert seen == ["o-1", "o-2"]
