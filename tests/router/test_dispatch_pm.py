"""R12: process-manager dispatch.

Process managers translate events from multiple source domains into commands
for target domains, maintaining their own state (rebuilt from PM-owned
``process_state`` events). Contract:

  - PM class decorated with ``@process_manager(name, pm_domain, sources, targets, state)``.
  - ``@handles(Evt)`` method receives ``(event, state, destinations)``.
  - Returns ``ProcessManagerResponse`` with commands, facts, and process_events.
  - State rebuilt per instance from ``request.process_state`` via ``@applies``.
  - Multi-PM merge: commands + facts concatenated across all matched handlers.
  - Rejection routing via ``@rejected(domain, command)`` as with command handlers.
"""

from __future__ import annotations

from dataclasses import dataclass

from google.protobuf.any_pb2 import Any as ProtoAny

from angzarr_client.router import ProcessManagerResponse
from angzarr_client.helpers import TYPE_URL_PREFIX
from angzarr_client.proto.angzarr import (
    CommandBook,
    CommandPage,
    Cover,
    EventBook,
    EventPage,
    PageHeader,
)
from angzarr_client.proto.angzarr import process_manager_pb2 as pm_pb
from angzarr_client.router import (
    Router,
    applies,
    handles,
    process_manager,
)
from tests.fixtures import (
    OrderCreated,
    OrderCompleted,
    ReserveStock,
    StockReserved,
    CreateShipment,
)


@dataclass
class WorkflowState:
    orders_seen: int = 0
    stock_reserved: bool = False


def _event_book(msgs: list, domain: str) -> EventBook:
    book = EventBook()
    book.cover.CopyFrom(Cover(domain=domain))
    for offset, m in enumerate(msgs):
        any_evt = ProtoAny()
        any_evt.type_url = TYPE_URL_PREFIX + m.DESCRIPTOR.full_name
        any_evt.value = m.SerializeToString()
        page = EventPage()
        page.header.CopyFrom(PageHeader(sequence=offset))
        page.event.CopyFrom(any_evt)
        book.pages.append(page)
    book.next_sequence = len(msgs)
    return book


def _pm_request(
    trigger_events: list,
    source_domain: str = "order",
    process_state_events: list | None = None,
    pm_domain: str = "fulfillment",
    dest_seqs: dict | None = None,
) -> pm_pb.ProcessManagerHandleRequest:
    req = pm_pb.ProcessManagerHandleRequest()
    req.trigger.CopyFrom(_event_book(trigger_events, domain=source_domain))
    req.process_state.CopyFrom(
        _event_book(process_state_events or [], domain=pm_domain)
    )
    for k, v in (dest_seqs or {}).items():
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
# Basic dispatch
# --------------------------------------------------------------------------


def test_pm_handler_invoked_for_matching_event():
    captured = {}

    @process_manager(
        name="pm-fulfillment",
        pm_domain="fulfillment",
        sources=["order", "inventory"],
        targets=["inventory"],
        state=WorkflowState,
    )
    class Fulfillment:
        @handles(OrderCreated)
        def on_order(self, event, state, destinations):
            captured["order_id"] = event.order_id
            return ProcessManagerResponse(
                commands=[
                    _command_book(
                        ReserveStock(order_id=event.order_id, quantity=1),
                        target_domain="inventory",
                    )
                ]
            )

    router = Router("pms").with_handler(Fulfillment, lambda: Fulfillment()).build()
    response = router.dispatch(
        _pm_request([OrderCreated(order_id="o-1", customer_id="c-1")])
    )

    assert captured["order_id"] == "o-1"
    assert len(response.commands) == 1
    assert response.commands[0].cover.domain == "inventory"


def test_pm_state_rebuild_from_process_state():
    captured = {}

    @process_manager(
        name="pm-x",
        pm_domain="fulfillment",
        sources=["order"],
        targets=["inventory"],
        state=WorkflowState,
    )
    class Fulfillment:
        @applies(OrderCompleted)
        def apply_completed(self, state, event):
            state.orders_seen += 1

        @handles(OrderCreated)
        def on_order(self, event, state, destinations):
            captured["state"] = state
            return None

    router = Router("pms").with_handler(Fulfillment, lambda: Fulfillment()).build()
    router.dispatch(
        _pm_request(
            [OrderCreated(order_id="x")],
            process_state_events=[
                OrderCompleted(order_id="a"),
                OrderCompleted(order_id="b"),
            ],
        )
    )

    assert captured["state"].orders_seen == 2


def test_pm_handler_receives_destinations():
    captured = {}

    @process_manager(
        name="pm-x",
        pm_domain="fulfillment",
        sources=["order"],
        targets=["inventory"],
        state=WorkflowState,
    )
    class P:
        @handles(OrderCreated)
        def on(self, event, state, destinations):
            captured["dest"] = destinations
            return None

    router = Router("pms").with_handler(P, lambda: P()).build()
    router.dispatch(
        _pm_request(
            [OrderCreated(order_id="o-1")],
            dest_seqs={"inventory": 12, "fulfillment": 5},
        )
    )

    assert captured["dest"].sequence_for("inventory") == 12
    assert captured["dest"].sequence_for("fulfillment") == 5


def test_pm_handler_receives_source_cover_when_declared():
    captured = {}

    @process_manager(
        name="pm-x",
        pm_domain="fulfillment",
        sources=["order"],
        targets=["inventory"],
        state=WorkflowState,
    )
    class P:
        @handles(OrderCreated)
        def on(self, event, state, destinations, source_cover=None):
            captured["source_cover"] = source_cover
            return None

    router = Router("pms").with_handler(P, P).build()
    req = _pm_request([OrderCreated(order_id="o-1")])
    req.trigger.cover.root.value = b"\x02" * 16

    router.dispatch(req)

    cover = captured["source_cover"]
    assert cover is not None
    assert cover.domain == "order"
    assert cover.root.value == b"\x02" * 16


def test_pm_handler_without_source_cover_param_unaffected():
    captured = {}

    @process_manager(
        name="pm-x",
        pm_domain="fulfillment",
        sources=["order"],
        targets=["inventory"],
        state=WorkflowState,
    )
    class P:
        @handles(OrderCreated)
        def on(self, event, state, destinations):
            captured["seen"] = True
            return None

    router = Router("pms").with_handler(P, P).build()
    router.dispatch(_pm_request([OrderCreated(order_id="o-1")]))
    assert captured == {"seen": True}


def test_pm_returning_none_yields_empty_response():
    @process_manager(
        name="pm-noop",
        pm_domain="fulfillment",
        sources=["order"],
        targets=["inventory"],
        state=WorkflowState,
    )
    class Noop:
        @handles(OrderCreated)
        def on(self, event, state, destinations):
            return None

    router = Router("pms").with_handler(Noop, lambda: Noop()).build()
    response = router.dispatch(_pm_request([OrderCreated(order_id="o-1")]))

    assert len(response.commands) == 0
    assert len(response.facts) == 0


def test_pm_multi_source_domain_routing():
    seen = []

    @process_manager(
        name="pm-multi",
        pm_domain="fulfillment",
        sources=["order", "inventory"],
        targets=["fulfillment"],
        state=WorkflowState,
    )
    class P:
        @handles(OrderCreated)
        def on_order(self, event, state, destinations):
            seen.append(("order", event.order_id))
            return None

        @handles(StockReserved)
        def on_stock(self, event, state, destinations):
            seen.append(("inventory", event.order_id))
            return None

    router = Router("pms").with_handler(P, lambda: P()).build()
    router.dispatch(_pm_request([OrderCreated(order_id="a")], source_domain="order"))
    router.dispatch(
        _pm_request(
            [StockReserved(order_id="b", sku="x", quantity=1)],
            source_domain="inventory",
        )
    )

    assert ("order", "a") in seen
    assert ("inventory", "b") in seen


# --------------------------------------------------------------------------
# Multi-PM merge
# --------------------------------------------------------------------------


def test_multiple_pms_both_invoked_in_registration_order():
    call_order = []

    @process_manager(
        name="pm-a",
        pm_domain="fulfillment",
        sources=["order"],
        targets=["inventory"],
        state=WorkflowState,
    )
    class A:
        @handles(OrderCreated)
        def on(self, event, state, destinations):
            call_order.append("A")
            return ProcessManagerResponse(
                commands=[
                    _command_book(
                        ReserveStock(order_id=event.order_id, quantity=1),
                        target_domain="inventory",
                    )
                ]
            )

    @process_manager(
        name="pm-b",
        pm_domain="shipping",
        sources=["order"],
        targets=["fulfillment"],
        state=WorkflowState,
    )
    class B:
        @handles(OrderCreated)
        def on(self, event, state, destinations):
            call_order.append("B")
            return ProcessManagerResponse(
                commands=[
                    _command_book(
                        CreateShipment(order_id=event.order_id, address="x"),
                        target_domain="fulfillment",
                    )
                ]
            )

    router = Router("pms").with_handler(A, lambda: A()).with_handler(B, lambda: B()).build()
    response = router.dispatch(_pm_request([OrderCreated(order_id="o-1")]))

    assert call_order == ["A", "B"]
    assert len(response.commands) == 2
    assert response.commands[0].cover.domain == "inventory"
    assert response.commands[1].cover.domain == "fulfillment"


# --------------------------------------------------------------------------
# Facts + process_events from PM response
# --------------------------------------------------------------------------


def test_pm_response_facts_and_process_events_propagate():
    @process_manager(
        name="pm-x",
        pm_domain="fulfillment",
        sources=["order"],
        targets=["inventory"],
        state=WorkflowState,
    )
    class P:
        @handles(OrderCreated)
        def on(self, event, state, destinations):
            pe = EventBook()
            pe.cover.CopyFrom(Cover(domain="fulfillment"))
            # Return a fact book + process-event book
            fact_book = EventBook()
            fact_book.cover.CopyFrom(Cover(domain="audit"))
            return ProcessManagerResponse(
                commands=[],
                process_events=pe,
                facts=[fact_book],
            )

    router = Router("pms").with_handler(P, lambda: P()).build()
    response = router.dispatch(_pm_request([OrderCreated(order_id="o-1")]))

    assert len(response.facts) == 1
    assert response.facts[0].cover.domain == "audit"
    assert response.process_events.cover.domain == "fulfillment"
