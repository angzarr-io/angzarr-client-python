"""Process-manager conformance harness — runs the shared
``conformance/features/process_manager.feature`` (the same one the Rust
cucumber-rs and Go godog harnesses run) against the Python binding via
pytest-bdd. Only the step layer is new; the behavior spec is shared,
unchanged."""

from __future__ import annotations

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from angzarr_client.proto.io.angzarr.v1 import process_manager_pb2, types_pb2
from angzarr_client.router import CodedError, Router

from ..builders import FQ_INCREASED, type_url
from ..fixture import AuditProcessManager, OrderProcessManager, recorded_states
from ..gen.test.counter import (
    audit_process_manager_angzarr,
    counter_pb2,
    order_process_manager_angzarr,
)
from . import CONFORMANCE_FEATURES

scenarios(str(CONFORMANCE_FEATURES / "process_manager.feature"))


class _World:
    """One scenario's state: a router the Given step registers the PM
    fixtures on, and the dispatch outcome."""

    def __init__(self):
        self.router = Router()
        self.resp = None
        self.err: CodedError | None = None

    def order_counts(self) -> list[int]:
        """The rebuilt count each reacting order PM recorded."""
        assert self.err is None, f"dispatch failed: {self.err}"
        return [
            s.count
            for s in recorded_states(self.resp, counter_pb2.OrderProcessManagerState)
        ]

    def audit_counts(self) -> list[int]:
        """How many events each reacting audit PM's rebuilt state saw."""
        assert self.err is None, f"dispatch failed: {self.err}"
        return [
            len(s.seen)
            for s in recorded_states(self.resp, counter_pb2.AuditProcessManagerState)
        ]

    def dispatch(self, request) -> None:
        try:
            self.resp = self.router.dispatch_process_manager(request)
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


def _trigger(domain: str, fqs: list[str], state=None, newest_seq: int | None = None):
    """A trigger of ``fqs`` pages in ``domain``, the newest at explicit
    sequence ``newest_seq`` when given, over the PM's prior ``state``."""
    req = process_manager_pb2.ProcessManagerHandleRequest()
    req.trigger.cover.domain = domain
    for fq in fqs:
        req.trigger.pages.add().payload.type_url = type_url(fq)
    if newest_seq is not None:
        req.trigger.pages[-1].sequence = newest_seq
    if state is not None:
        req.process_state.CopyFrom(state)
    return req


def _state_of(n: int, owner: str | None = None):
    """A process-state book of n Increased events; ``owner`` sets its cover
    domain (the PM the state belongs to)."""
    book = types_pb2.EventBook()
    if owner is not None:
        book.cover.domain = owner
    for _ in range(n):
        book.pages.add().payload.type_url = type_url(FQ_INCREASED)
    return book


@given("an order process-manager")
def _an_order_pm(world):
    order_process_manager_angzarr.register_order_process_manager(
        world.router, OrderProcessManager()
    )


@given("co-resident order and audit process-managers")
def _co_resident_pms(world):
    order_process_manager_angzarr.register_order_process_manager(
        world.router, OrderProcessManager()
    )
    audit_process_manager_angzarr.register_audit_process_manager(
        world.router, AuditProcessManager()
    )


@when(
    parsers.re(
        r'an Increased trigger in domain "(?P<domain>[^"]*)" at sequence (?P<seq>\d+) is dispatched'
    )
)
def _increased_at(world, domain, seq):
    world.dispatch(_trigger(domain, [FQ_INCREASED], newest_seq=int(seq)))


@when(parsers.re(r'an Increased trigger in domain "(?P<domain>[^"]*)" is dispatched$'))
def _increased_in_domain(world, domain):
    world.dispatch(_trigger(domain, [FQ_INCREASED]))


@when("a trigger whose newest page is an undeclared event is dispatched")
def _newest_undeclared(world):
    world.dispatch(_trigger("counter", [FQ_INCREASED, "test.counter.Unwatched"]))


@when(
    parsers.re(
        r"an Increased trigger is dispatched over a prior state of (?P<n>\d+) events"
    )
)
def _increased_over_state(world, n):
    world.dispatch(_trigger("counter", [FQ_INCREASED], state=_state_of(int(n))))


@when(
    parsers.re(
        r'an Increased trigger is dispatched over a prior "(?P<owner>[^"]*)" state of (?P<n>\d+) events'
    )
)
def _increased_over_owned_state(world, owner, n):
    world.dispatch(_trigger("counter", [FQ_INCREASED], state=_state_of(int(n), owner)))


@when("a request with no trigger is dispatched")
def _no_trigger(world):
    world.dispatch(process_manager_pb2.ProcessManagerHandleRequest())


@when("a trigger with no pages is dispatched")
def _empty_trigger(world):
    req = process_manager_pb2.ProcessManagerHandleRequest()
    req.trigger.SetInParent()
    world.dispatch(req)


@then(parsers.re(r'the process-manager emits one command to "(?P<target>[^"]*)"'))
def _emits_one_command(world, target):
    assert world.err is None, f"dispatch failed: {world.err}"
    assert len(world.resp.commands) == 1
    assert world.resp.commands[0].cover.domain == target


@then("the process-manager emits no commands")
def _emits_no_commands(world):
    assert world.err is None, f"dispatch failed: {world.err}"
    assert len(world.resp.commands) == 0


@then(parsers.re(r"the process-manager rebuilt (?P<n>\d+) prior state events"))
def _rebuilt_n(world, n):
    assert world.order_counts() == [int(n)]


@then(parsers.re(r"the order process-manager rebuilt (?P<n>\d+) prior state events"))
def _order_rebuilt_n(world, n):
    assert world.order_counts() == [int(n)]


@then(parsers.re(r"the audit process-manager rebuilt (?P<n>\d+) prior state events"))
def _audit_rebuilt_n(world, n):
    assert world.audit_counts() == [int(n)]


@then("the order process-manager did not react")
def _order_did_not_react(world):
    assert world.order_counts() == []
    assert len(world.resp.commands) == 0


@then("the command carries no router-stamped provenance")
def _no_stamped_provenance(world):
    assert world.err is None, f"dispatch failed: {world.err}"
    page = world.resp.commands[0].page
    assert not page.HasField("command_id"), "the coordinator stamps the command id"
    assert not page.HasField("correlation_id"), "the coordinator stamps the correlation"


@then("the process event carries no cover")
def _process_event_unaddressed(world):
    assert world.err is None, f"dispatch failed: {world.err}"
    assert len(world.resp.process_events) == 1
    assert not world.resp.process_events[0].HasField("cover")


@then(parsers.re(r"the dispatch fails with (?P<code>[A-Z_]+)"))
def _fails_with(world, code):
    assert world.err is not None, f"expected failure {code}, got a success"
    assert world.err.code == code
