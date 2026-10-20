"""Dispatch through the hand-written registration APIs (no generated wiring):
the trampoline's boundary guard against exceptions that are not
``Exception`` subclasses, the arguments saga and process-manager handlers
see, and the commands they emit passing through as emitted."""

from __future__ import annotations

import pytest

from angzarr_client.proto.io.angzarr.v1 import process_manager_pb2, types_pb2
from angzarr_client.router import (
    AggregateDispatch,
    CodedError,
    GrpcCode,
    ProcessManagerDispatch,
    Rebuilder,
    Router,
    SagaDispatch,
)

from . import builders
from .gen.test.counter import counter_pb2


class _HandlerAbort(BaseException):
    """A BaseException that is not an Exception, raised by a handler."""


def _counter_dispatch() -> AggregateDispatch:
    return AggregateDispatch(
        name="CounterAggregate",
        domain="counter",
        rebuilder=Rebuilder(factory=counter_pb2.CounterState),
    )


def test_handler_base_exception_surfaces_as_internal_failure():
    """A handler raising a BaseException that is not an Exception fails the
    dispatch as an unclassified INTERNAL error rather than passing as
    success."""

    def abort(cmd, state, cctx):
        raise _HandlerAbort("handler aborted")

    dispatch = _counter_dispatch().on_command(builders.FQ_INCREASE_BY, abort)
    with Router() as router:
        router.register_aggregate(dispatch)
        with pytest.raises(CodedError) as exc:
            router.dispatch(builders.increase_command(1))

    assert exc.value.code == "UNHANDLED_HANDLER_ERROR"
    assert exc.value.grpc == GrpcCode.INTERNAL
    assert "handler aborted" in exc.value.message


def test_the_binding_hands_handlers_no_destinations():
    # Emitted commands carry no destination state or sequences, so handlers
    # receive no Destinations; the declared targets stay on the registration.
    import angzarr_client.router as router_module

    assert not hasattr(router_module, "Destinations")


def _saga_source(seq: int):
    source = types_pb2.EventBook()
    source.cover.domain = "order"
    page = source.pages.add()
    page.payload.type_url = builders.type_url(builders.FQ_INCREASED)
    page.sequence = seq
    return source


def _reserve_to(domain: str):
    cmd = types_pb2.CommandBook()
    cmd.cover.domain = domain
    cmd.page.command.payload.type_url = builders.type_url(builders.FQ_RESERVE)
    return cmd


def test_saga_handler_sees_the_source_cover_and_commands_pass_through():
    seen: list[str] = []

    def translate(event, source_cover):
        seen.append(source_cover.domain)
        return [_reserve_to("inventory"), _reserve_to("billing")]

    saga = SagaDispatch("order-saga", "order", targets=["inventory", "billing"])
    saga.on_event(builders.FQ_INCREASED, translate)
    with Router() as router:
        router.register_saga(saga)
        resp = router.dispatch_saga(_saga_source(7))

    assert seen == ["order"]
    assert list(resp.commands) == [_reserve_to("inventory"), _reserve_to("billing")]


def test_no_registration_has_a_rejection_or_compensation_api():
    tables = (
        SagaDispatch("s", "order"),
        _counter_dispatch(),
        ProcessManagerDispatch("p", "p", Rebuilder(factory=counter_pb2.CounterState)),
    )
    for table in tables:
        for removed in ("on_rejected", "on_undo"):
            assert not hasattr(table, removed), (type(table).__name__, removed)


def _pm_trigger(seq: int):
    req = process_manager_pb2.ProcessManagerHandleRequest()
    req.trigger.cover.domain = "counter"
    page = req.trigger.pages.add()
    page.payload.type_url = builders.type_url(builders.FQ_INCREASED)
    page.sequence = seq
    return req


def test_process_manager_handler_sees_the_event_and_state():
    seen: list[str] = []

    def react(event, state):
        seen.append(event.type_url)
        resp = process_manager_pb2.ProcessManagerHandleResponse()
        resp.commands.append(_reserve_to("inventory"))
        return resp

    pm = ProcessManagerDispatch(
        "Reserving",
        "reserving-pm",
        Rebuilder(factory=counter_pb2.CounterState),
        ["inventory"],
    )
    pm.on_event("counter", builders.FQ_INCREASED, react)
    with Router() as router:
        router.register_process_manager(pm)
        resp = router.dispatch_process_manager(_pm_trigger(3))

    assert seen == [builders.type_url(builders.FQ_INCREASED)]
    assert list(resp.commands) == [_reserve_to("inventory")]


def test_process_manager_targets_default_to_none_declared():
    seen: list[str] = []

    def react(event, state):
        seen.append(event.type_url)
        return process_manager_pb2.ProcessManagerHandleResponse()

    pm = ProcessManagerDispatch(
        "Quiet", "quiet-pm", Rebuilder(factory=counter_pb2.CounterState)
    )
    pm.on_event("counter", builders.FQ_INCREASED, react)
    with Router() as router:
        router.register_process_manager(pm)
        router.dispatch_process_manager(_pm_trigger(0))

    assert seen == [builders.type_url(builders.FQ_INCREASED)]
