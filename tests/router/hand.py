"""Components written by hand: the router's dispatch tables over the
handler-contract fixture messages and ordinary handler functions, no
angzarr-cli output (tests/router/test_hand_written.py hosts them,
tests/router/child_hand.py runs them as child processes)."""

from __future__ import annotations

from angzarr_client.proto.io.angzarr.v1 import types_pb2
from angzarr_client.router import (
    FactRecord,
    PolicyDispatch,
    ProcessManagerDispatch,
    ProjectorDispatch,
    Rebuilder,
    pack,
)

from .gen import counter_pb2, order_pb2

INCREASED = counter_pb2.Increased.DESCRIPTOR.full_name


def totals() -> ProjectorDispatch:
    def increased(view, _event):
        view.count += 1

    return (
        ProjectorDispatch("Totals", counter_pb2.CounterProjectorState)
        .for_domains("alpha", "beta")
        .on_event(INCREASED, increased)
    )


def reserve(domain: str) -> types_pb2.CommandBook:
    command = types_pb2.CommandBook()
    command.cover.domain = domain
    command.page.command.payload.CopyFrom(pack(counter_pb2.Reserve()))
    return command


def reserving() -> PolicyDispatch:
    return (
        PolicyDispatch("Reserving", "alpha", "beta")
        .on_event(INCREASED, lambda _event, _cover: [reserve("beta")])
        .on_refused(
            counter_pb2.Reserve.DESCRIPTOR.full_name,
            lambda _refusal, _ctx: order_pb2.StockUnavailable(item="x"),
        )
    )


def fulfilment() -> ProcessManagerDispatch:
    def started_one(state, _event):
        state.placed += 1

    def start(_command, state, _ctx):
        assert state.placed == 0
        book = types_pb2.EventBook()
        book.pages.add().payload.CopyFrom(pack(order_pb2.FulfillmentStarted()))
        return book

    def delivered(fact, _state, _ctx):
        return FactRecord(fact, [order_pb2.FulfillmentStarted()])

    rebuilder = Rebuilder(order_pb2.FulfillmentState).apply(
        order_pb2.FulfillmentStarted.DESCRIPTOR.full_name, started_one
    )
    return (
        ProcessManagerDispatch("Fulfilment", "fulfilment", rebuilder)
        .on_command(order_pb2.StartFulfillment.DESCRIPTOR.full_name, start)
        .on_fact(order_pb2.ItemDelivered.DESCRIPTOR.full_name, delivered)
    )
