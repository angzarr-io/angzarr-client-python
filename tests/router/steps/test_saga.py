"""Saga conformance harness — runs the shared
``conformance/features/saga.feature`` (the same one the Rust cucumber-rs and Go
godog harnesses run) against the Python binding via pytest-bdd. Only the step
layer is new; the behavior spec is shared, unchanged."""

from __future__ import annotations

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from angzarr_client.proto.io.angzarr.v1 import types_pb2
from angzarr_client.router import CodedError, Router

from ..builders import FQ_INCREASED, FQ_RESERVE, type_url
from ..fixture import OrderSaga
from ..gen.test.counter import order_saga_angzarr
from . import CONFORMANCE_FEATURES

scenarios(str(CONFORMANCE_FEATURES / "saga.feature"))


class _World:
    """One scenario's state: a router with the saga fixture registered (its
    Increased handler recording the source sequence it is given), and the
    dispatch outcome."""

    def __init__(self):
        self.router = Router()
        self.seen: list[int] = []
        saga = order_saga_angzarr.new_order_saga_dispatch(OrderSaga())
        generated = saga.events[FQ_INCREASED]

        def increased(event, source):
            self.seen.append(source.sequence)
            return generated(event, source.cover)

        saga.on_event_with_context(FQ_INCREASED, increased)
        self.router.register_saga(saga)
        self.resp = None
        self.err: CodedError | None = None

    def dispatch(self, request) -> None:
        try:
            self.resp = self.router.dispatch_saga(request)
            self.err = None
        except CodedError as exc:
            self.err = exc
            self.resp = None

    def close(self) -> None:
        self.router.close()


@pytest.fixture
def world():
    w = _World()
    yield w
    w.close()


def _event_source(fq: str, seq: int | None = None):
    """A source EventBook carrying one event of fq in the "order" domain, at
    explicit sequence ``seq`` when given."""
    source = types_pb2.EventBook()
    source.cover.domain = "order"
    page = source.pages.add()
    page.payload.type_url = type_url(fq)
    if seq is not None:
        page.sequence = seq
    return source


@given(parsers.re(r'an order saga delivering to "(?P<target>[^"]*)"'))
def _an_order_saga(world, target):
    # Each scenario's fresh saga is registered in _World.__init__.
    pass


@when(parsers.re(r"an Increased event at sequence (?P<seq>\d+) is dispatched"))
def _increased_at(world, seq):
    world.dispatch(_event_source(FQ_INCREASED, int(seq)))


@when("a Reserve event is dispatched")
def _reserve_event(world):
    world.dispatch(_event_source(FQ_RESERVE))


@when("a source with no pages is dispatched")
def _empty_source(world):
    world.dispatch(types_pb2.EventBook())


@then(parsers.re(r'the saga emits one command to "(?P<target>[^"]*)"'))
def _emits_one_command(world, target):
    assert world.err is None, f"dispatch failed: {world.err}"
    assert len(world.resp.commands) == 1
    assert world.resp.commands[0].cover.domain == target


@then("the command carries no router-stamped provenance")
def _no_stamped_provenance(world):
    assert world.err is None, f"dispatch failed: {world.err}"
    page = world.resp.commands[0].page
    assert not page.HasField("command_id"), "the coordinator stamps the command id"
    assert not page.HasField("correlation_id"), "the coordinator stamps the correlation"


@then(parsers.re(r"the saga handler saw source sequence (?P<seq>\d+)"))
def _handler_saw_sequence(world, seq):
    assert world.err is None, f"dispatch failed: {world.err}"
    assert world.seen == [int(seq)]


@then("the saga emits no commands")
def _emits_no_commands(world):
    assert world.err is None, f"dispatch failed: {world.err}"
    assert len(world.resp.commands) == 0


@then(parsers.re(r"the dispatch fails with (?P<code>[A-Z_]+)"))
def _fails_with(world, code):
    assert world.err is not None, f"expected failure {code}, got a success"
    assert world.err.code == code
