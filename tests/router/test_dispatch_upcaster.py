"""Upcaster dispatch + @upcaster / @upcasts decorators + builder wiring.

Upcasters transform old event versions to current versions on replay. The
unified Router carries them alongside the other four kinds:

  - ``@upcaster(name, domain)`` class decorator stamps kind metadata.
  - ``@upcasts(FromType, ToType)`` method decorator registers a transform.
  - ``Router(...).with_handler(U, U).build()`` returns an ``UpcasterRouter``.
  - Dispatch consumes an ``UpcastRequest`` (domain + pages) and returns an
    ``UpcastResponse`` (pages), transforming events whose proto type-URL
    matches a registered ``FromType``. Events without a registered transform
    pass through unchanged, preserving ``PageHeader`` + ``created_at``.
  - Handlers whose ``@upcaster(domain=...)`` does not match ``request.domain``
    are skipped entirely (events pass through).
"""

from __future__ import annotations

import pytest
from google.protobuf.any_pb2 import Any as ProtoAny
from google.protobuf.timestamp_pb2 import Timestamp

from angzarr_client.helpers import TYPE_URL_PREFIX
from angzarr_client.proto.angzarr import (
    EventPage,
    PageHeader,
    UpcastRequest,
    UpcastResponse,
)
from angzarr_client.router import (
    BuildError,
    Router,
    UpcasterRouter,
    command_handler,
    handles,
    upcaster,
    upcasts,
)
from tests.fixtures import (
    OrderCreated,
    OrderCreatedV1,
    PlayerRegistered,
    PlayerRegisteredV1,
    StockReserved,
)


def _page(msg, sequence: int = 0, created_at_seconds: int = 0) -> EventPage:
    any_evt = ProtoAny()
    any_evt.type_url = TYPE_URL_PREFIX + msg.DESCRIPTOR.full_name
    any_evt.value = msg.SerializeToString()
    page = EventPage()
    page.header.CopyFrom(PageHeader(sequence=sequence))
    page.event.CopyFrom(any_evt)
    if created_at_seconds:
        page.created_at.CopyFrom(Timestamp(seconds=created_at_seconds))
    return page


def _request(pages: list[EventPage], domain: str = "order") -> UpcastRequest:
    return UpcastRequest(domain=domain, events=pages)


# --------------------------------------------------------------------------
# @upcaster class decorator
# --------------------------------------------------------------------------


def test_upcaster_sets_kind_and_meta():
    @upcaster(name="upcaster-order", domain="order")
    class OrderUpcaster:
        pass

    assert OrderUpcaster.__angzarr_kind__ == "upcaster"
    assert OrderUpcaster.__angzarr_meta__ == {
        "name": "upcaster-order",
        "domain": "order",
    }


def test_upcaster_requires_name_and_domain_kwargs():
    with pytest.raises(TypeError):
        upcaster(domain="order")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        upcaster(name="upcaster-order")  # type: ignore[call-arg]


def test_upcaster_rejects_stacking_with_other_kind():
    with pytest.raises(TypeError):

        @upcaster(name="x", domain="x")
        @command_handler(domain="x", state=type("S", (), {}))
        class Mixed:
            pass


# --------------------------------------------------------------------------
# @upcasts method decorator
# --------------------------------------------------------------------------


def test_upcasts_stamps_from_and_to_types():
    @upcasts(OrderCreatedV1, OrderCreated)
    def migrate(self, old):
        return old

    assert migrate.__angzarr_upcasts__ == (OrderCreatedV1, OrderCreated)


def test_upcasts_conflicts_with_other_method_decorators():
    with pytest.raises(TypeError):

        @upcasts(OrderCreatedV1, OrderCreated)
        @handles(OrderCreated)
        def both(self, evt):
            return evt


# --------------------------------------------------------------------------
# Builder wiring
# --------------------------------------------------------------------------


def test_builder_returns_upcaster_router_for_upcaster_kind():
    @upcaster(name="upcaster-order", domain="order")
    class Up:
        pass

    result = Router("upcaster-order").with_handler(Up, Up).build()
    assert isinstance(result, UpcasterRouter)


def test_builder_rejects_empty_name():
    @upcaster(name="", domain="order")
    class Up:
        pass

    with pytest.raises(BuildError):
        Router("x").with_handler(Up, Up).build()


def test_builder_rejects_empty_domain():
    @upcaster(name="upcaster-order", domain="")
    class Up:
        pass

    with pytest.raises(BuildError):
        Router("x").with_handler(Up, Up).build()


def test_builder_rejects_mixed_kinds():
    @upcaster(name="upcaster-order", domain="order")
    class Up:
        pass

    @command_handler(domain="order", state=type("S", (), {}))
    class Agg:
        pass

    with pytest.raises(BuildError):
        Router("x").with_handler(Up, Up).with_handler(Agg, Agg).build()


# --------------------------------------------------------------------------
# Dispatch — transformation happy path
# --------------------------------------------------------------------------


def test_dispatch_transforms_matching_event():
    @upcaster(name="upcaster-order", domain="order")
    class OrderUpcaster:
        @upcasts(OrderCreatedV1, OrderCreated)
        def migrate_created(self, old: OrderCreatedV1) -> OrderCreated:
            return OrderCreated(
                order_id=old.order_id,
                customer_id=old.customer_id,
                total=0,
            )

    router = Router("upcaster-order").with_handler(OrderUpcaster, OrderUpcaster).build()
    page = _page(OrderCreatedV1(order_id="o-1", customer_id="c-1"))
    response = router.dispatch(_request([page]))

    assert isinstance(response, UpcastResponse)
    assert len(response.events) == 1
    result = response.events[0]
    assert result.event.type_url == TYPE_URL_PREFIX + "order.OrderCreated"


def test_dispatch_passes_through_unregistered_type():
    @upcaster(name="upcaster-order", domain="order")
    class OrderUpcaster:
        @upcasts(OrderCreatedV1, OrderCreated)
        def migrate(self, old):
            return OrderCreated(order_id=old.order_id)

    router = Router("upcaster-order").with_handler(OrderUpcaster, OrderUpcaster).build()
    # StockReserved has no registered transform → passthrough.
    page = _page(StockReserved(order_id="o-1", sku="sku", quantity=1))
    response = router.dispatch(_request([page]))

    assert len(response.events) == 1
    assert (
        response.events[0].event.type_url == TYPE_URL_PREFIX + "inventory.StockReserved"
    )


def test_dispatch_preserves_page_header_and_timestamp():
    @upcaster(name="upcaster-order", domain="order")
    class OrderUpcaster:
        @upcasts(OrderCreatedV1, OrderCreated)
        def migrate(self, old):
            return OrderCreated(order_id=old.order_id)

    router = Router("upcaster-order").with_handler(OrderUpcaster, OrderUpcaster).build()
    page = _page(OrderCreatedV1(order_id="o-1"), sequence=42, created_at_seconds=1234)
    response = router.dispatch(_request([page]))

    assert response.events[0].header.sequence == 42
    assert response.events[0].created_at.seconds == 1234


def test_dispatch_preserves_order_across_mixed_events():
    @upcaster(name="upcaster-order", domain="order")
    class OrderUpcaster:
        @upcasts(OrderCreatedV1, OrderCreated)
        def migrate(self, old):
            return OrderCreated(order_id=old.order_id)

    router = Router("upcaster-order").with_handler(OrderUpcaster, OrderUpcaster).build()
    pages = [
        _page(OrderCreatedV1(order_id="a"), sequence=0),
        _page(StockReserved(order_id="a", sku="x", quantity=1), sequence=1),
        _page(OrderCreatedV1(order_id="b"), sequence=2),
    ]
    response = router.dispatch(_request(pages))

    type_urls = [p.event.type_url for p in response.events]
    assert type_urls == [
        TYPE_URL_PREFIX + "order.OrderCreated",
        TYPE_URL_PREFIX + "inventory.StockReserved",
        TYPE_URL_PREFIX + "order.OrderCreated",
    ]
    sequences = [p.header.sequence for p in response.events]
    assert sequences == [0, 1, 2]


def test_dispatch_skips_page_without_event():
    @upcaster(name="upcaster-order", domain="order")
    class OrderUpcaster:
        @upcasts(OrderCreatedV1, OrderCreated)
        def migrate(self, old):
            return OrderCreated(order_id=old.order_id)

    router = Router("upcaster-order").with_handler(OrderUpcaster, OrderUpcaster).build()
    empty = EventPage()
    empty.header.CopyFrom(PageHeader(sequence=7))
    response = router.dispatch(_request([empty]))

    assert len(response.events) == 1
    assert response.events[0].header.sequence == 7
    assert not response.events[0].HasField("event")


def test_dispatch_skips_handler_with_different_domain():
    @upcaster(name="upcaster-player", domain="player")
    class PlayerUpcaster:
        @upcasts(PlayerRegisteredV1, PlayerRegistered)
        def migrate(self, old):
            return PlayerRegistered(
                player_id=old.player_id, display_name=old.display_name
            )

    router = (
        Router("upcaster-player").with_handler(PlayerUpcaster, PlayerUpcaster).build()
    )
    # Request targets "order" domain — player handler must be skipped even
    # though it has an @upcasts method matching a proto type.
    page = _page(OrderCreatedV1(order_id="o-1"))
    response = router.dispatch(_request([page], domain="order"))

    assert response.events[0].event.type_url == TYPE_URL_PREFIX + "order.OrderCreatedV1"


def test_dispatch_passthrough_with_empty_upcaster():
    """An upcaster with zero @upcasts methods is a valid passthrough."""

    @upcaster(name="upcaster-order", domain="order")
    class PassThru:
        pass

    router = Router("upcaster-order").with_handler(PassThru, PassThru).build()
    page = _page(OrderCreatedV1(order_id="o-1"))
    response = router.dispatch(_request([page]))

    assert len(response.events) == 1
    assert response.events[0].event.type_url == TYPE_URL_PREFIX + "order.OrderCreatedV1"


# --------------------------------------------------------------------------
# gRPC adapter wiring
# --------------------------------------------------------------------------


def test_upcaster_grpc_adapter_delegates_to_router():
    from unittest.mock import Mock

    from angzarr_client.router.server import UpcasterGrpc

    @upcaster(name="upcaster-order", domain="order")
    class OrderUpcaster:
        @upcasts(OrderCreatedV1, OrderCreated)
        def migrate(self, old):
            return OrderCreated(order_id=old.order_id)

    router = Router("upcaster-order").with_handler(OrderUpcaster, OrderUpcaster).build()
    servicer = UpcasterGrpc(router)

    request = _request([_page(OrderCreatedV1(order_id="o-1"))])
    context = Mock()
    response = servicer.Upcast(request, context)

    context.abort.assert_not_called()
    assert isinstance(response, UpcastResponse)
    assert len(response.events) == 1
    assert response.events[0].event.type_url == TYPE_URL_PREFIX + "order.OrderCreated"


def test_upcaster_grpc_adapter_translates_exception_to_internal():
    from unittest.mock import Mock

    import grpc

    from angzarr_client.router.server import UpcasterGrpc

    @upcaster(name="upcaster-order", domain="order")
    class Broken:
        @upcasts(OrderCreatedV1, OrderCreated)
        def migrate(self, old):
            raise RuntimeError("oops")

    router = Router("upcaster-order").with_handler(Broken, Broken).build()
    servicer = UpcasterGrpc(router)

    request = _request([_page(OrderCreatedV1(order_id="o-1"))])
    context = Mock()
    context.abort.side_effect = grpc.RpcError
    import pytest as _pytest

    with _pytest.raises(grpc.RpcError):
        servicer.Upcast(request, context)

    context.abort.assert_called_once()
    args = context.abort.call_args
    assert args.args[0] == grpc.StatusCode.INTERNAL


# --------------------------------------------------------------------------
# Chain semantics — audit finding #43 (cucumber C-0136 / C-0137).
# Every matching upcaster runs in registration order; output of one is
# the input of the next. Mirrors Rust's `router/upcaster.rs::dispatch`.
# --------------------------------------------------------------------------


def test_dispatch_chains_v1_through_v2_to_v3():
    """C-0136: V1 → V2 → V3 transforms compose across two upcasters."""
    from tests.fixtures import OrderCompleted

    @upcaster(name="upcaster-v1-v2", domain="order")
    class V1ToV2:
        @upcasts(OrderCreatedV1, OrderCreated)
        def migrate(self, old: OrderCreatedV1) -> OrderCreated:
            return OrderCreated(order_id=old.order_id, customer_id=old.customer_id)

    @upcaster(name="upcaster-v2-v3", domain="order")
    class V2ToV3:
        @upcasts(OrderCreated, OrderCompleted)
        def migrate(self, old: OrderCreated) -> OrderCompleted:
            return OrderCompleted(order_id=old.order_id)

    # Registration order: V1ToV2 first, then V2ToV3 — chain follows.
    router = (
        Router("upcaster-chain")
        .with_handler(V1ToV2, V1ToV2)
        .with_handler(V2ToV3, V2ToV3)
        .build()
    )
    page = _page(OrderCreatedV1(order_id="o-1", customer_id="c-1"))
    response = router.dispatch(_request([page]))

    assert len(response.events) == 1
    assert (
        response.events[0].event.type_url
        == TYPE_URL_PREFIX + "order.OrderCompleted"
    ), "chain must reach V3 (OrderCompleted), not stop at V2 (OrderCreated)"


def test_dispatch_chain_stops_when_no_further_upcaster_matches():
    """C-0137: a chain stops at whatever stage no upcaster matches the
    current event's type_url. Subsequent upcasters that don't match the
    current value are simply skipped."""
    @upcaster(name="upcaster-v1-v2", domain="order")
    class V1ToV2:
        @upcasts(OrderCreatedV1, OrderCreated)
        def migrate(self, old: OrderCreatedV1) -> OrderCreated:
            return OrderCreated(order_id=old.order_id)

    @upcaster(name="upcaster-other", domain="order")
    class OtherChain:
        # This handler's From-type doesn't match OrderCreated, so it
        # won't fire even after V1ToV2 transforms the event.
        @upcasts(PlayerRegisteredV1, PlayerRegistered)
        def migrate(self, old: PlayerRegisteredV1) -> PlayerRegistered:
            return PlayerRegistered(player_id=old.player_id)

    router = (
        Router("upcaster-chain")
        .with_handler(V1ToV2, V1ToV2)
        .with_handler(OtherChain, OtherChain)
        .build()
    )
    page = _page(OrderCreatedV1(order_id="o-1"))
    response = router.dispatch(_request([page]))

    assert len(response.events) == 1
    assert (
        response.events[0].event.type_url == TYPE_URL_PREFIX + "order.OrderCreated"
    ), "chain stops at V2 (OrderCreated) — OtherChain doesn't match"


def test_dispatch_chain_independent_of_registration_order_within_factory():
    """A single upcaster with multiple @upcasts methods picks the one
    matching the current event type. The inner-loop break preserves
    'one transform per upcaster per page'."""
    from tests.fixtures import OrderCompleted

    @upcaster(name="upcaster-multi", domain="order")
    class MultiUp:
        @upcasts(OrderCreatedV1, OrderCreated)
        def from_v1(self, old: OrderCreatedV1) -> OrderCreated:
            return OrderCreated(order_id=old.order_id)

        @upcasts(OrderCreated, OrderCompleted)
        def from_v2(self, old: OrderCreated) -> OrderCompleted:
            return OrderCompleted(order_id=old.order_id)

    router = Router("u").with_handler(MultiUp, MultiUp).build()

    # Send V1 — only the V1→V2 method on this upcaster fires once.
    # Chain doesn't continue within a single factory invocation.
    response = router.dispatch(_request([_page(OrderCreatedV1(order_id="o-1"))]))
    assert (
        response.events[0].event.type_url == TYPE_URL_PREFIX + "order.OrderCreated"
    ), "single factory fires one method per page; doesn't loop within itself"
