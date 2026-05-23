"""Step defs for features/client/upcaster.feature.

C-0123..C-0125 pin the symbol + attribute surface (decorator shape).
C-0136..C-0137 pin dispatch chain semantics (audit finding #43).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from google.protobuf.any_pb2 import Any as ProtoAny
from pytest_bdd import given, parsers, scenarios, then, when

from angzarr_client import state_factory, upcaster, upcasts
from angzarr_client.helpers import TYPE_URL_PREFIX
from angzarr_client.proto.angzarr.v1.types_pb2 import EventPage, PageHeader
from angzarr_client.proto.angzarr.v1.upcaster_pb2 import UpcastRequest
from angzarr_client.router import Router

scenarios("upcaster.feature")


@dataclass
class _State:
    cls: Any = None
    fn: Any = None
    errors: list = field(default_factory=list)
    # Chain dispatch (C-0136 / C-0137).
    chain_classes: list[type] = field(default_factory=list)
    chain_event_msg: Any = None
    chain_response: Any = None


@pytest.fixture
def state() -> _State:
    return _State()


class _FromType:
    pass


class _ToType:
    pass


@given(
    parsers.re(r'an upcaster named "(?P<up_name>[^"]+)" in domain "(?P<domain>[^"]+)"')
)
def _given_upcaster_class(state: _State, up_name: str, domain: str) -> None:
    try:

        @upcaster(name=up_name, domain=domain)
        class UpcasterCls:
            pass

        state.cls = UpcasterCls
    except Exception as exc:
        state.errors.append(exc)


@given(
    parsers.re(r'an upcasting rule from "(?P<from_name>[^"]+)" to "(?P<to_name>[^"]+)"')
)
def _given_upcasts_method(state: _State, from_name: str, to_name: str) -> None:
    try:

        @upcasts(_FromType, _ToType)
        def upgrade(old):
            return old

        state.fn = upgrade
    except Exception as exc:
        state.errors.append(exc)


@given("an upcaster with a state factory")
def _given_state_factory_method(state: _State) -> None:
    try:

        @state_factory
        def empty_state():
            return object()

        state.fn = empty_state
    except Exception as exc:
        state.errors.append(exc)


@then("the declaration is accepted")
def _then_declaration_accepted(state: _State) -> None:
    """Single business-outcome assertion replacing the legacy split
    ``the class declaration compiles without error`` /
    ``the method declaration compiles without error``. Either a class
    or method may have been declared depending on which Given step ran;
    accepting at least one being populated keeps the assertion uniform.
    """
    assert not state.errors, f"declaration raised: {state.errors}"
    assert state.cls is not None or state.fn is not None


# --------------------------------------------------------------------------
# C-0136 / C-0137: chain dispatch semantics (audit finding #43).
# Concrete protobuf types come from the `angzarr.examples` test fixtures
# (OrderCreatedV1 / OrderCreated / OrderCompleted); the cucumber prose
# uses abstract V1/V2/V3 names so the same scenarios can be ported to
# Rust where prost generates analogous types.
# --------------------------------------------------------------------------


def _register_v1_v2(state: _State) -> None:
    from tests.fixtures import OrderCreated, OrderCreatedV1

    @upcaster(name="upcaster-v1-v2", domain="order")
    class V1ToV2:
        @upcasts(OrderCreatedV1, OrderCreated)
        def migrate(self, old: OrderCreatedV1) -> OrderCreated:
            return OrderCreated(order_id=old.order_id, customer_id=old.customer_id)

    state.chain_classes.append(V1ToV2)


def _register_v2_v3(state: _State) -> None:
    from tests.fixtures import OrderCompleted, OrderCreated

    @upcaster(name="upcaster-v2-v3", domain="order")
    class V2ToV3:
        @upcasts(OrderCreated, OrderCompleted)
        def migrate(self, old: OrderCreated) -> OrderCompleted:
            return OrderCompleted(order_id=old.order_id)

    state.chain_classes.append(V2ToV3)


def _register_v3_v4(state: _State) -> None:
    # No fixture pair beyond V3 (OrderCompleted) exists, so the
    # "V3 → V4" upcaster is registered against an unrelated From-type
    # so it never matches V1's chain — exercising the "chain stops"
    # case from C-0137.
    from tests.fixtures import PlayerRegistered, PlayerRegisteredV1

    @upcaster(name="upcaster-other", domain="order")
    class V3ToV4:
        @upcasts(PlayerRegisteredV1, PlayerRegistered)
        def migrate(self, old: PlayerRegisteredV1) -> PlayerRegistered:
            return PlayerRegistered(player_id=old.player_id)

    state.chain_classes.append(V3ToV4)


@given("an upcaster registered for V1 → V2")
def _given_v1_v2(state: _State) -> None:
    _register_v1_v2(state)


@given("an upcaster registered for V2 → V3")
def _given_v2_v3(state: _State) -> None:
    _register_v2_v3(state)


@given("an upcaster registered for V3 → V4")
def _given_v3_v4(state: _State) -> None:
    _register_v3_v4(state)


@given("an incoming event of type V1")
def _given_incoming_v1(state: _State) -> None:
    from tests.fixtures import OrderCreatedV1

    state.chain_event_msg = OrderCreatedV1(order_id="o-1", customer_id="c-1")


@when("the V1 event is upcasted")
def _when_dispatch_chain(state: _State) -> None:
    builder = Router("upcaster-chain")
    for cls in state.chain_classes:
        builder = builder.with_handler(cls, cls)
    router = builder.build()

    msg = state.chain_event_msg
    any_evt = ProtoAny()
    any_evt.type_url = TYPE_URL_PREFIX + msg.DESCRIPTOR.full_name
    any_evt.value = msg.SerializeToString()
    page = EventPage()
    page.header.CopyFrom(PageHeader(sequence=0))
    page.event.CopyFrom(any_evt)

    state.chain_response = router.dispatch(UpcastRequest(domain="order", events=[page]))


@then(parsers.parse("the emitted event has type {version}"))
def _then_emitted_type(state: _State, version: str) -> None:
    assert state.chain_response is not None
    assert len(state.chain_response.events) == 1
    type_url = state.chain_response.events[0].event.type_url
    expected = {
        "V1": TYPE_URL_PREFIX + "order.OrderCreatedV1",
        "V2": TYPE_URL_PREFIX + "order.OrderCreated",
        "V3": TYPE_URL_PREFIX + "order.OrderCompleted",
    }[version]
    assert type_url == expected, f"got {type_url!r}, expected {expected!r}"
