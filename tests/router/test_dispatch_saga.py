"""R11: saga dispatch.

Sagas translate events from one domain into commands for another. Contract:

  - Saga class decorated with ``@saga(name, source, target)``.
  - ``@handles(Evt)`` method receives ``(event, destinations)``.
  - Returns ``CommandBook`` (or tuple of CommandBooks) for emitted commands;
    ``EventBook`` (or tuple) for injected facts; or ``None`` for no output.
  - ``SagaRouter.dispatch(SagaHandleRequest)`` returns a ``SagaResponse``
    with commands + events merged across all matched sagas.
  - Multi-saga merge: two sagas in the same source domain both ``@handles(Evt)``
    are both invoked in registration order; outputs concatenated.
"""

from __future__ import annotations


from google.protobuf.any_pb2 import Any as ProtoAny

from angzarr_client.helpers import TYPE_URL_PREFIX
from angzarr_client.proto.angzarr import (
    CommandBook,
    CommandPage,
    Cover,
    EventBook,
    EventPage,
    PageHeader,
    SagaHandleRequest,
)
from angzarr_client.router import (
    Router,
    handles,
    saga,
)
from tests.fixtures import (
    OrderCreated,
    OrderCompleted,
    ReserveStock,
    CreateShipment,
)


def _event_book_with(event_msgs: list, domain: str = "order") -> EventBook:
    book = EventBook()
    book.cover.CopyFrom(Cover(domain=domain))
    for offset, msg in enumerate(event_msgs):
        any_evt = ProtoAny()
        any_evt.type_url = TYPE_URL_PREFIX + msg.DESCRIPTOR.full_name
        any_evt.value = msg.SerializeToString()
        page = EventPage()
        page.header.CopyFrom(PageHeader(sequence=offset))
        page.event.CopyFrom(any_evt)
        book.pages.append(page)
    book.next_sequence = len(event_msgs)
    return book


def _saga_request(
    event_msgs: list, source_domain: str = "order", dest_seqs: dict | None = None
) -> SagaHandleRequest:
    req = SagaHandleRequest()
    req.source.CopyFrom(_event_book_with(event_msgs, domain=source_domain))
    if dest_seqs:
        for k, v in dest_seqs.items():
            req.destination_sequences[k] = v
    return req


def _command_book(cmd_msg, target_domain: str, seq: int = 0) -> CommandBook:
    any_cmd = ProtoAny()
    any_cmd.type_url = TYPE_URL_PREFIX + cmd_msg.DESCRIPTOR.full_name
    any_cmd.value = cmd_msg.SerializeToString()
    page = CommandPage()
    page.header.CopyFrom(PageHeader(sequence=seq))
    page.command.CopyFrom(any_cmd)
    book = CommandBook()
    book.cover.CopyFrom(Cover(domain=target_domain))
    book.pages.append(page)
    return book


# --------------------------------------------------------------------------
# Single-saga dispatch
# --------------------------------------------------------------------------


def test_saga_handler_invoked_for_matching_event():
    captured = {}

    @saga(name="saga-order-fulfillment", source="order", target="inventory")
    class Fulfillment:
        @handles(OrderCreated)
        def translate(self, event, destinations):
            captured["event_order_id"] = event.order_id
            return _command_book(
                ReserveStock(order_id=event.order_id, sku="sku-1", quantity=1),
                target_domain="inventory",
            )

    router = Router("sagas").with_handler(Fulfillment, lambda: Fulfillment()).build()
    response = router.dispatch(
        _saga_request([OrderCreated(order_id="o-1", customer_id="c-1")])
    )

    assert captured["event_order_id"] == "o-1"
    assert len(response.commands) == 1
    assert response.commands[0].cover.domain == "inventory"


def test_saga_handler_receives_destinations():
    captured = {}

    @saga(name="saga-x", source="order", target="inventory")
    class S:
        @handles(OrderCreated)
        def translate(self, event, destinations):
            captured["destinations"] = destinations
            return None

    router = Router("sagas").with_handler(S, lambda: S()).build()
    router.dispatch(
        _saga_request(
            [OrderCreated(order_id="o-1")],
            dest_seqs={"inventory": 7, "fulfillment": 3},
        )
    )

    d = captured["destinations"]
    assert d.sequence_for("inventory") == 7
    assert d.sequence_for("fulfillment") == 3


def test_saga_handler_receives_source_cover_when_declared():
    captured = {}

    @saga(name="saga-x", source="order", target="inventory")
    class S:
        @handles(OrderCreated)
        def translate(self, event, destinations, source_cover=None):
            captured["source_cover"] = source_cover
            return None

    router = Router("sagas").with_handler(S, S).build()
    req = _saga_request([OrderCreated(order_id="o-1")], source_domain="order")
    # Stamp a root onto the source cover to exercise the pass-through.
    req.source.cover.root.value = b"\x01" * 16

    router.dispatch(req)

    cover = captured["source_cover"]
    assert cover is not None
    # B2: handlers receive a Cover wrapper; accessors are method calls,
    # raw proto fields go through ``.proto()``.
    assert cover.domain() == "order"
    assert cover.proto().root.value == b"\x01" * 16


def test_saga_handler_without_source_cover_param_unaffected():
    """Handlers without ``source_cover`` in their signature keep receiving
    just ``(event, destinations)`` — opt-in contract."""
    captured = {}

    @saga(name="saga-x", source="order", target="inventory")
    class S:
        @handles(OrderCreated)
        def translate(self, event, destinations):
            captured["seen"] = True
            return None

    router = Router("sagas").with_handler(S, S).build()
    router.dispatch(_saga_request([OrderCreated(order_id="o-1")]))
    assert captured == {"seen": True}


def test_saga_returning_none_emits_empty_response():
    @saga(name="s", source="order", target="inventory")
    class Noop:
        @handles(OrderCreated)
        def on(self, event, destinations):
            return None

    router = Router("sagas").with_handler(Noop, lambda: Noop()).build()
    response = router.dispatch(_saga_request([OrderCreated(order_id="o-1")]))

    assert len(response.commands) == 0


def test_unknown_event_type_yields_no_calls():
    call_order = []

    @saga(name="s", source="order", target="inventory")
    class S:
        @handles(OrderCreated)
        def on(self, event, destinations):
            call_order.append("called")
            return None

    router = Router("sagas").with_handler(S, lambda: S()).build()
    # Send an OrderCompleted event — no @handles for it.
    response = router.dispatch(_saga_request([OrderCompleted(order_id="o-1")]))

    assert call_order == []
    assert len(response.commands) == 0


# --------------------------------------------------------------------------
# Multi-saga merge
# --------------------------------------------------------------------------


def test_multiple_sagas_same_source_both_invoked_in_registration_order():
    call_order = []

    @saga(name="s-fulfill", source="order", target="inventory")
    class Fulfillment:
        @handles(OrderCreated)
        def on(self, event, destinations):
            call_order.append("fulfill")
            return _command_book(
                ReserveStock(order_id=event.order_id, quantity=1),
                target_domain="inventory",
            )

    @saga(name="s-ship", source="order", target="fulfillment")
    class Shipping:
        @handles(OrderCreated)
        def on(self, event, destinations):
            call_order.append("ship")
            return _command_book(
                CreateShipment(order_id=event.order_id, address="addr"),
                target_domain="fulfillment",
            )

    router = (
        Router("sagas")
        .with_handler(Fulfillment, lambda: Fulfillment())
        .with_handler(Shipping, lambda: Shipping())
        .build()
    )
    response = router.dispatch(_saga_request([OrderCreated(order_id="o-1")]))

    assert call_order == ["fulfill", "ship"]
    assert len(response.commands) == 2
    assert response.commands[0].cover.domain == "inventory"
    assert response.commands[1].cover.domain == "fulfillment"


def test_saga_emitting_tuple_of_commands_yields_multiple_commands():
    @saga(name="s", source="order", target="inventory")
    class Multi:
        @handles(OrderCreated)
        def on(self, event, destinations):
            return (
                _command_book(ReserveStock(order_id="o-1", sku="a"), "inventory"),
                _command_book(ReserveStock(order_id="o-1", sku="b"), "inventory"),
            )

    router = Router("sagas").with_handler(Multi, lambda: Multi()).build()
    response = router.dispatch(_saga_request([OrderCreated(order_id="o-1")]))

    assert len(response.commands) == 2


# --------------------------------------------------------------------------
# Last event in the book is the trigger
# --------------------------------------------------------------------------


def test_saga_trigger_is_last_event_in_source_book():
    seen_ids = []

    @saga(name="s", source="order", target="inventory")
    class S:
        @handles(OrderCreated)
        def on(self, event, destinations):
            seen_ids.append(event.order_id)
            return None

    router = Router("sagas").with_handler(S, lambda: S()).build()
    # Book has three OrderCreated events; saga should see only the last.
    response = router.dispatch(
        _saga_request(
            [
                OrderCreated(order_id="first"),
                OrderCreated(order_id="middle"),
                OrderCreated(order_id="last"),
            ]
        )
    )

    assert seen_ids == ["last"]
    assert len(response.commands) == 0


# --------------------------------------------------------------------------
# Malformed input — P2.5 / audit finding #12
# --------------------------------------------------------------------------


def test_saga_dispatch_raises_on_missing_source():
    """Per audit P2.5: malformed input raises DispatchError(INVALID_ARGUMENT)
    matching Rust's `extract_saga_event_type_url`. Previously Python
    silently returned an empty SagaResponse."""
    import grpc
    import pytest

    from angzarr_client.router.dispatch import DispatchError

    @saga(name="s", source="order", target="inventory")
    class S:
        @handles(OrderCreated)
        def on(self, event, destinations):
            return None

    router = Router("sagas").with_handler(S, lambda: S()).build()
    request = SagaHandleRequest()  # source field unset

    with pytest.raises(DispatchError) as exc:
        router.dispatch(request)
    assert exc.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert "missing saga source" in exc.value.details()


def test_saga_dispatch_raises_on_empty_pages():
    import grpc
    import pytest

    from angzarr_client.router.dispatch import DispatchError

    @saga(name="s", source="order", target="inventory")
    class S:
        @handles(OrderCreated)
        def on(self, event, destinations):
            return None

    router = Router("sagas").with_handler(S, lambda: S()).build()
    request = SagaHandleRequest()
    request.source.cover.CopyFrom(Cover(domain="order"))
    # No pages

    with pytest.raises(DispatchError) as exc:
        router.dispatch(request)
    assert exc.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert "empty saga source" in exc.value.details()


def test_saga_dispatch_raises_on_missing_event_payload():
    import grpc
    import pytest

    from angzarr_client.router.dispatch import DispatchError

    @saga(name="s", source="order", target="inventory")
    class S:
        @handles(OrderCreated)
        def on(self, event, destinations):
            return None

    router = Router("sagas").with_handler(S, lambda: S()).build()
    request = SagaHandleRequest()
    request.source.cover.CopyFrom(Cover(domain="order"))
    page = request.source.pages.add()
    page.header.sequence = 0
    # No event nor external payload

    with pytest.raises(DispatchError) as exc:
        router.dispatch(request)
    assert exc.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert "missing event payload" in exc.value.details()


def test_saga_dispatch_garbage_payload_surfaces_invalid_argument():
    """Audit #87: malformed Any.value bytes raise DispatchError(
    INVALID_ARGUMENT, code=ANY_DECODE_FAILED). Pre-fix the unguarded
    ParseFromString propagated google.protobuf.message.DecodeError up
    to _translate_and_abort's catch-all and landed as INTERNAL — Rust's
    macro-emitted dispatch returns INVALID_ARGUMENT from the same site.
    """
    import grpc
    import pytest

    from angzarr_client.error_codes import codes, keys
    from angzarr_client.router.dispatch import DispatchError

    @saga(name="s", source="order", target="inventory")
    class S:
        @handles(OrderCreated)
        def on(self, event, destinations):
            return None

    router = Router("sagas").with_handler(S, lambda: S()).build()
    request = SagaHandleRequest()
    request.source.cover.CopyFrom(Cover(domain="order"))
    page = request.source.pages.add()
    page.header.sequence = 0
    # Malformed Any: matching type_url, garbage bytes.
    page.event.type_url = TYPE_URL_PREFIX + OrderCreated.DESCRIPTOR.full_name
    page.event.value = b"\xff\xff\xff\xff"  # not a valid OrderCreated body

    with pytest.raises(DispatchError) as exc:
        router.dispatch(request)
    assert exc.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert exc.value.error_code == codes.ANY_DECODE_FAILED
    assert keys.TYPE_URL in exc.value.extras
    assert keys.CAUSE in exc.value.extras
