"""A compensation handler reads the rejecting handler's code from
RejectionNotification.code (spec C-0505 / C-0506): the router passes the
notification through the binding unchanged, code and human-readable reason in
separate fields."""

from __future__ import annotations

from angzarr_client.proto.io.angzarr.v1 import command_handler_pb2
from angzarr_client.router import AggregateDispatch, Rebuilder, Router

from . import builders
from .gen.test.counter import counter_pb2


def _seen_rejection(code: str, reason: str):
    seen = []

    def compensate(notification, rejection, state, cctx):
        seen.append((rejection.code, rejection.rejection_reason))
        return command_handler_pb2.BusinessResponse()

    dispatch = AggregateDispatch(
        "CounterAggregate", "counter", Rebuilder(counter_pb2.CounterState)
    )
    dispatch.on_rejected(builders.FQ_RESERVE, compensate)
    with Router() as router:
        router.register_aggregate(dispatch)
        router.dispatch(builders.rejection_command(builders.FQ_RESERVE, code, reason))
    return seen


def test_the_compensator_reads_the_code_and_the_reason_separately():
    assert _seen_rejection("OUT_OF_STOCK", "not enough stock") == [
        ("OUT_OF_STOCK", "not enough stock")
    ]


def test_a_rejection_without_a_code_reaches_the_compensator_with_an_empty_code():
    assert _seen_rejection("", "unclassified failure") == [("", "unclassified failure")]
