"""What each host callback receives across the FFI and what the router hands
back: the exact Any (type URL and payload bytes) every thunk sees, the
notification / rejection / compensate / rebuilt state compensators and undo
handlers see, the page context each callback runs under, registration
descriptors the core acts on (projector name, unknown-event hook, PM
snapshot loader, PM names for rejection routing), registration failures, and
router lifecycle."""

from __future__ import annotations

import pytest

from angzarr_client.proto.io.angzarr.v1 import (
    command_handler_pb2,
    process_manager_pb2,
    saga_pb2,
    types_pb2,
)
from angzarr_client.router import (
    AggregateDispatch,
    CodedError,
    FactRecord,
    GrpcCode,
    PageContext,
    ProcessManagerDispatch,
    ProjectorDispatch,
    Rebuilder,
    Router,
    SagaDispatch,
    current_cover,
    current_page,
    pack,
)

from . import builders
from .gen.test.counter import counter_pb2

# Payload bytes that are not the empty encoding, so a dropped value shows.
_PAYLOAD = counter_pb2.CounterState(count=42).SerializeToString()
_INCREASED_URL = builders.type_url(builders.FQ_INCREASED)


def _event_page(seq: int, value: bytes = _PAYLOAD):
    page = types_pb2.EventPage()
    page.header.sequence = seq
    page.event.type_url = _INCREASED_URL
    page.event.value = value
    return page


def _count_increases(state, _event):
    state.count += 1


def _counting_rebuilder() -> Rebuilder:
    return Rebuilder(factory=counter_pb2.CounterState).apply(
        builders.FQ_INCREASED, _count_increases
    )


# --- the Any each thunk receives ---


def test_aggregate_thunks_receive_the_exact_type_url_and_payload():
    seen = []

    def load_snapshot(state, snapshot):
        seen.append(("snapshot", snapshot.type_url, snapshot.value))
        state.ParseFromString(snapshot.value)

    def apply_increased(state, event):
        seen.append(("apply", event.type_url, event.value))

    def handle(cmd, state, cctx):
        seen.append(("command", cmd.type_url, cmd.value))

    rebuilder = (
        Rebuilder(factory=counter_pb2.CounterState)
        .apply(builders.FQ_INCREASED, apply_increased)
        .with_snapshot(load_snapshot)
    )
    dispatch = AggregateDispatch("Ledger", "ledger", rebuilder).on_command(
        builders.FQ_INCREASE_BY, handle
    )
    cc = types_pb2.ContextualCommand()
    cc.command.cover.domain = "ledger"
    cc.command.pages.add().command.CopyFrom(pack(counter_pb2.IncreaseBy(n=3)))
    cc.events.snapshot.sequence = 0
    cc.events.snapshot.state.CopyFrom(pack(counter_pb2.CounterState(count=7)))
    cc.events.pages.append(_event_page(1))
    cc.events.next_sequence = 2
    with Router() as router:
        router.register_aggregate(dispatch)
        router.dispatch(cc)

    assert seen == [
        (
            "snapshot",
            "/test.counter.CounterState",
            counter_pb2.CounterState(count=7).SerializeToString(),
        ),
        ("apply", _INCREASED_URL, _PAYLOAD),
        (
            "command",
            "/test.counter.IncreaseBy",
            counter_pb2.IncreaseBy(n=3).SerializeToString(),
        ),
    ]


def test_command_payloads_of_one_byte_and_of_none_cross_intact():
    seen = []
    dispatch = AggregateDispatch(
        "Ledger", "ledger", Rebuilder(factory=counter_pb2.CounterState)
    ).on_command(builders.FQ_INCREASE_BY, lambda cmd, _s, _c: seen.append(cmd.value))
    with Router() as router:
        router.register_aggregate(dispatch)
        for value in (b"\x01", b""):
            cc = builders.increase_command(1)
            cc.command.pages[0].command.value = value
            router.dispatch(cc)

    assert seen == [b"\x01", b""]


def test_fact_handler_receives_the_fact_payload():
    seen = []

    def observe(fact, state):
        seen.append((fact.type_url, fact.value))
        return FactRecord.as_received(fact)

    req = command_handler_pb2.FactRequest()
    req.facts.cover.domain = "ledger"
    req.facts.pages.append(_event_page(0))
    dispatch = AggregateDispatch("Ledger", "ledger", _counting_rebuilder()).on_fact(
        builders.FQ_INCREASED, observe
    )
    with Router() as router:
        router.register_aggregate(dispatch)
        book = router.dispatch_fact(req)

    assert seen == [(_INCREASED_URL, _PAYLOAD)]
    assert book.pages[0].event.value == _PAYLOAD


def test_projector_fold_receives_the_exact_type_url_and_payload():
    seen = []
    projector = ProjectorDispatch("Tracker", lambda: None).on_event(
        builders.FQ_INCREASED, lambda _s, e: seen.append((e.type_url, e.value))
    )
    book = types_pb2.EventBook()
    book.cover.domain = "counter"
    book.pages.append(_event_page(0))
    with Router() as router:
        router.register_projector(projector)
        router.dispatch_projector(book)

    assert seen == [(_INCREASED_URL, _PAYLOAD)]


def _saga_request(label: str = "order-1"):
    req = saga_pb2.SagaHandleRequest()
    req.source.cover.CopyFrom(builders.cover_of("order", label))
    req.source.pages.append(_event_page(4))
    return req


def test_saga_receives_the_exact_event_and_its_returned_events_are_kept():
    seen = []

    def translate(event, dests, source_cover):
        seen.append((event.type_url, event.value))
        emitted = types_pb2.EventBook()
        emitted.cover.domain = "order-audit"
        return [], [emitted]

    saga = SagaDispatch("order-saga", "order").on_event(
        builders.FQ_INCREASED, translate
    )
    with Router() as router:
        router.register_saga(saga)
        resp = router.dispatch_saga(_saga_request())

    assert seen == [(_INCREASED_URL, _PAYLOAD)]
    assert [b.cover.domain for b in resp.events] == ["order-audit"]


def test_saga_context_handler_sees_its_declared_destinations():
    seen = []

    def translate(event, dests, source: PageContext):
        seen.append((dests.domains, source.sequence))
        return [], []

    saga = SagaDispatch(
        "order-saga", "order", targets=["inventory"]
    ).on_event_with_context(builders.FQ_INCREASED, translate)
    with Router() as router:
        router.register_saga(saga)
        router.dispatch_saga(_saga_request())

    assert seen == [(["inventory"], 4)]


def test_a_saga_emitting_nothing_returns_an_empty_response():
    saga = SagaDispatch("order-saga", "order").on_event(
        builders.FQ_INCREASED, lambda _e, _d, _c: ([], [])
    )
    with Router() as router:
        router.register_saga(saga)
        resp = router.dispatch_saga(_saga_request())

    assert resp.SerializeToString() == b""


def test_process_manager_handler_receives_the_exact_event():
    seen = []

    def react(event, state, dests):
        seen.append((event.type_url, event.value))
        return process_manager_pb2.ProcessManagerHandleResponse()

    pm = ProcessManagerDispatch(
        "Reserving", "reserving-pm", Rebuilder(factory=counter_pb2.CounterState)
    ).on_event("counter", builders.FQ_INCREASED, react)
    req = process_manager_pb2.ProcessManagerHandleRequest()
    req.trigger.cover.domain = "counter"
    req.trigger.pages.append(_event_page(2))
    with Router() as router:
        router.register_process_manager(pm)
        router.dispatch_process_manager(req)

    assert seen == [(_INCREASED_URL, _PAYLOAD)]


# --- aggregate compensators and undo handlers ---


def _payment_command(payload, prior: int):
    """A notification command to the payment aggregate over ``prior``
    Increased events."""
    notification = types_pb2.Notification()
    notification.payload.CopyFrom(payload)
    cc = types_pb2.ContextualCommand()
    cc.command.cover.CopyFrom(builders.cover_of("payment", "payment-1"))
    cc.command.pages.add().command.CopyFrom(pack(notification))
    for seq in range(prior):
        cc.events.pages.append(_event_page(seq))
    cc.events.next_sequence = prior
    return cc


def _reserve_rejection():
    rejection = types_pb2.RejectionNotification(rejection_reason="out of stock")
    rejection.rejected_command.cover.domain = "inventory"
    rejection.rejected_command.pages.add().command.type_url = builders.type_url(
        builders.FQ_RESERVE
    )
    return rejection


def test_compensator_sees_the_notification_rejection_rebuilt_state_and_cover():
    seen = []

    def compensate(notification, rejection, state, cctx):
        seen.append(
            (
                notification.payload.type_url,
                rejection.rejection_reason,
                rejection.rejected_command.cover.domain,
                state.count,
                current_cover().root.value,
            )
        )

    dispatch = AggregateDispatch(
        "Payment", "payment", _counting_rebuilder()
    ).on_rejected(builders.FQ_RESERVE, compensate)
    with Router() as router:
        router.register_aggregate(dispatch)
        router.dispatch(_payment_command(pack(_reserve_rejection()), 2))

    assert seen == [
        (
            "/io.angzarr.v1.RejectionNotification",
            "out of stock",
            "inventory",
            2,
            builders.root_of("payment-1"),
        )
    ]


def test_undo_handler_sees_the_notification_compensate_rebuilt_state_and_cover():
    seen = []

    def undo(notification, compensate, state, cctx):
        seen.append(
            (
                notification.payload.type_url,
                compensate.command_type,
                compensate.reason,
                state.count,
                current_cover().root.value,
            )
        )

    compensate = types_pb2.Compensate(
        command_type=builders.FQ_RESERVE, sequences=[0], reason="aborted"
    )
    dispatch = AggregateDispatch("Payment", "payment", _counting_rebuilder()).on_undo(
        builders.FQ_RESERVE, undo
    )
    with Router() as router:
        router.register_aggregate(dispatch)
        router.dispatch(_payment_command(pack(compensate), 3))

    assert seen == [
        (
            "/io.angzarr.v1.Compensate",
            builders.FQ_RESERVE,
            "aborted",
            3,
            builders.root_of("payment-1"),
        )
    ]


def test_co_resident_aggregates_compensating_one_rejection_keep_separate_state():
    seen = []

    class Ledger:
        pass

    def by_message(notification, rejection, state, cctx):
        seen.append(("message", type(state).__name__, state.count))

    def by_object(notification, rejection, state, cctx):
        seen.append(("object", type(state).__name__))

    first = AggregateDispatch("Payment", "payment", _counting_rebuilder()).on_rejected(
        builders.FQ_RESERVE, by_message
    )
    second = AggregateDispatch(
        "PaymentLedger", "payment", Rebuilder(factory=Ledger)
    ).on_rejected(builders.FQ_RESERVE, by_object)
    with Router() as router:
        router.register_aggregate(first)
        router.register_aggregate(second)
        router.dispatch(_payment_command(pack(_reserve_rejection()), 2))

    assert sorted(seen) == [("message", "CounterState", 2), ("object", "Ledger")]


# --- process-manager compensators ---


def _pm_rejection_request(component: str = "", process_events: int = 0):
    rejection = _reserve_rejection()
    if component:
        header = rejection.rejected_command.pages[0].header
        header.angzarr_deferred.source_component = component
        header.angzarr_deferred.source.domain = "counter"
    notification = types_pb2.Notification()
    notification.payload.CopyFrom(pack(rejection))
    req = process_manager_pb2.ProcessManagerHandleRequest()
    req.trigger.cover.CopyFrom(builders.cover_of("reserving-pm", "pm-1"))
    page = req.trigger.pages.add()
    page.header.sequence = 5
    page.event.CopyFrom(pack(notification))
    for seq in range(process_events):
        req.process_state.pages.append(_event_page(seq))
    req.process_state.next_sequence = process_events
    return req


def test_pm_compensator_sees_the_notification_rejection_and_rebuilt_state():
    seen = []

    def compensate(notification, rejection, state):
        seen.append(
            (
                notification.payload.type_url,
                rejection.rejection_reason,
                state.count,
                current_cover().root.value,
            )
        )

    pm = ProcessManagerDispatch(
        "Reserving", "reserving-pm", _counting_rebuilder()
    ).on_rejected(builders.FQ_RESERVE, compensate)
    with Router() as router:
        router.register_process_manager(pm)
        router.dispatch_process_manager(_pm_rejection_request(process_events=2))

    assert seen == [
        (
            "/io.angzarr.v1.RejectionNotification",
            "out of stock",
            2,
            builders.root_of("pm-1"),
        )
    ]


def test_pm_compensator_pair_with_an_escalation_carries_the_escalation():
    def compensate(notification, rejection, state):
        book = types_pb2.EventBook()
        book.cover.domain = "reserving-pm"
        escalation = types_pb2.Notification()
        escalation.cover.domain = "escalated"
        return [book], escalation

    pm = ProcessManagerDispatch(
        "Reserving", "reserving-pm", Rebuilder(factory=counter_pb2.CounterState)
    ).on_rejected(builders.FQ_RESERVE, compensate)
    with Router() as router:
        router.register_process_manager(pm)
        resp = router.dispatch_process_manager(_pm_rejection_request())

    assert [b.cover.domain for b in resp.process_events] == ["reserving-pm"]
    assert resp.notification.cover.domain == "escalated"


def test_a_rejection_naming_its_issuing_pm_runs_only_that_pm():
    calls = []

    def compensator(name):
        def compensate(notification, rejection, state):
            calls.append(name)

        return compensate

    with Router() as router:
        for name in ("Reserving", "Auditing"):
            router.register_process_manager(
                ProcessManagerDispatch(
                    name, "reserving-pm", Rebuilder(factory=counter_pb2.CounterState)
                ).on_rejected(builders.FQ_RESERVE, compensator(name))
            )
        router.dispatch_process_manager(_pm_rejection_request(component="Auditing"))

    assert calls == ["Auditing"]


def test_pm_snapshot_seeds_the_state_its_handler_sees():
    seen = []

    def load_snapshot(state, snapshot):
        seen.append(("snapshot", snapshot.type_url))
        state.ParseFromString(snapshot.value)

    def react(event, state, dests):
        seen.append(("handler", state.count))
        return process_manager_pb2.ProcessManagerHandleResponse()

    pm = ProcessManagerDispatch(
        "Reserving",
        "reserving-pm",
        _counting_rebuilder().with_snapshot(load_snapshot),
    ).on_event("counter", builders.FQ_INCREASED, react)
    req = process_manager_pb2.ProcessManagerHandleRequest()
    req.trigger.cover.domain = "counter"
    req.trigger.pages.append(_event_page(9))
    req.process_state.snapshot.sequence = 4
    req.process_state.snapshot.state.CopyFrom(pack(counter_pb2.CounterState(count=10)))
    req.process_state.pages.append(_event_page(5))
    req.process_state.next_sequence = 6
    with Router() as router:
        router.register_process_manager(pm)
        router.dispatch_process_manager(req)

    assert seen == [("snapshot", "/test.counter.CounterState"), ("handler", 11)]


# --- fact handler failures ---


def test_a_fact_handler_returning_a_non_record_names_the_type_in_its_failure():
    req = command_handler_pb2.FactRequest()
    req.facts.cover.domain = "ledger"
    req.facts.pages.append(_event_page(0))
    dispatch = AggregateDispatch("Ledger", "ledger", _counting_rebuilder()).on_fact(
        builders.FQ_INCREASED, lambda fact, _state: fact
    )
    with Router() as router:
        router.register_aggregate(dispatch)
        with pytest.raises(CodedError) as exc:
            router.dispatch_fact(req)

    assert exc.value.code == "UNHANDLED_HANDLER_ERROR"
    assert exc.value.message == (
        "fact handler for '/test.counter.Increased' returned Any, not a FactRecord"
    )


# --- replay ---


def test_replay_with_no_history_packs_a_fresh_state():
    with Router() as router:
        router.register_aggregate(
            AggregateDispatch("Ledger", "ledger", _counting_rebuilder())
        )
        router.register_process_manager(
            ProcessManagerDispatch("Reserving", "reserving-pm", _counting_rebuilder())
        )
        empty = command_handler_pb2.ReplayRequest()
        by_aggregate = router.dispatch_replay("ledger", empty)
        by_pm = router.dispatch_replay("reserving-pm", empty)

    for resp in (by_aggregate, by_pm):
        assert resp.state.type_url == "/test.counter.CounterState"
        assert counter_pb2.CounterState.FromString(resp.state.value).count == 0


def test_replay_snapshot_loader_runs_under_an_empty_page_context():
    seen = []

    def load_snapshot(state, snapshot):
        seen.append(current_page())
        state.ParseFromString(snapshot.value)

    req = command_handler_pb2.ReplayRequest()
    req.base_snapshot.state.CopyFrom(pack(counter_pb2.CounterState(count=5)))
    with Router() as router:
        router.register_aggregate(
            AggregateDispatch(
                "Ledger", "ledger", _counting_rebuilder().with_snapshot(load_snapshot)
            )
        )
        resp = router.dispatch_replay("ledger", req)

    assert seen == [PageContext()]
    assert counter_pb2.CounterState.FromString(resp.state.value).count == 5


# --- the dispatch-level page context ---


def test_snapshot_loader_sees_the_command_cover():
    seen = []

    def load_snapshot(state, snapshot):
        seen.append(current_cover().root.value)

    dispatch = AggregateDispatch(
        "Ledger", "ledger", _counting_rebuilder().with_snapshot(load_snapshot)
    ).on_command(builders.FQ_INCREASE_BY, lambda _c, _s, _x: None)
    cc = types_pb2.ContextualCommand()
    cc.command.cover.CopyFrom(builders.cover_of("ledger", "ledger-1"))
    cc.command.pages.add().command.CopyFrom(pack(counter_pb2.IncreaseBy(n=1)))
    cc.events.snapshot.state.CopyFrom(pack(counter_pb2.CounterState(count=1)))
    cc.events.next_sequence = 1
    with Router() as router:
        router.register_aggregate(dispatch)
        router.dispatch(cc)

    assert seen == [builders.root_of("ledger-1")]


def test_fact_handler_sees_the_facts_cover():
    seen = []

    def observe(fact, state):
        seen.append(current_cover().root.value)
        return FactRecord.as_received(fact)

    req = command_handler_pb2.FactRequest()
    req.facts.cover.CopyFrom(builders.cover_of("ledger", "facts-1"))
    req.facts.pages.append(_event_page(0))
    dispatch = AggregateDispatch("Ledger", "ledger", _counting_rebuilder()).on_fact(
        builders.FQ_INCREASED, observe
    )
    with Router() as router:
        router.register_aggregate(dispatch)
        router.dispatch_fact(req)

    assert seen == [builders.root_of("facts-1")]


def test_projector_finisher_sees_the_book_cover():
    seen = []

    def finish(state, events):
        seen.append(current_cover().root.value)
        return types_pb2.Projection(projector="tracker")

    projector = ProjectorDispatch("Tracker", lambda: None).finish(finish)
    book = types_pb2.EventBook()
    book.cover.CopyFrom(builders.cover_of("counter", "counter-1"))
    book.pages.append(_event_page(0))
    with Router() as router:
        router.register_projector(projector)
        router.dispatch_projector(book)

    assert seen == [builders.root_of("counter-1")]


def test_a_handler_on_a_coverless_book_sees_no_cover():
    seen = []

    def handle(cmd, state, cctx):
        seen.append((cctx.cover, current_page().cover))
        with pytest.raises(
            RuntimeError, match="^the book being handled carries no cover$"
        ):
            current_cover()

    dispatch = AggregateDispatch(
        "Ledger", "ledger", Rebuilder(factory=counter_pb2.CounterState)
    ).on_command(builders.FQ_INCREASE_BY, handle)
    cc = types_pb2.ContextualCommand()
    cc.command.pages.add().command.CopyFrom(pack(counter_pb2.IncreaseBy(n=1)))
    with Router() as router:
        router.register_aggregate(dispatch)
        router.dispatch(cc)

    assert seen == [(None, None)]


def test_current_page_outside_a_dispatch_names_the_problem():
    with pytest.raises(
        RuntimeError, match="^no angzarr dispatch is being handled on this thread$"
    ):
        current_page()


# --- projector registration ---


def test_projector_unknown_hook_observes_each_unhandled_type_url():
    seen = []
    projector = ProjectorDispatch("Tracker", lambda: None).on_unknown(seen.append)
    book = types_pb2.EventBook()
    book.cover.domain = "counter"
    book.pages.append(_event_page(0))
    reserve = book.pages.add()
    reserve.event.CopyFrom(pack(counter_pb2.Reserve()))
    with Router() as router:
        router.register_projector(projector)
        router.dispatch_projector(book)

    assert seen == [_INCREASED_URL, "/test.counter.Reserve"]


def test_a_projector_without_a_finisher_stamps_its_name_on_the_projection():
    book = types_pb2.EventBook()
    book.cover.domain = "counter"
    book.pages.append(_event_page(0))
    with Router() as router:
        router.register_projector(ProjectorDispatch("Tracker", lambda: None))
        proj = router.dispatch_projector(book)

    assert proj.projector == "Tracker"
    assert proj.cover.domain == "counter"


# --- registration failures ---


def test_a_second_projector_is_refused_with_invalid_argument():
    with Router() as router:
        router.register_projector(ProjectorDispatch("Tracker", lambda: None))
        with pytest.raises(CodedError) as exc:
            router.register_projector(ProjectorDispatch("Other", lambda: None))

    assert exc.value.grpc == GrpcCode.INVALID_ARGUMENT


def test_a_second_claim_of_a_command_type_is_refused_with_invalid_argument():
    def ledger(name):
        return AggregateDispatch(
            name, "ledger", Rebuilder(factory=counter_pb2.CounterState)
        ).on_command(builders.FQ_INCREASE_BY, lambda _c, _s, _x: None)

    with Router() as router:
        router.register_aggregate(ledger("Ledger"))
        with pytest.raises(CodedError) as exc:
            router.register_aggregate(ledger("Shadow"))

    assert exc.value.grpc == GrpcCode.INVALID_ARGUMENT


def test_an_ambiguous_pm_compensation_is_refused_with_invalid_argument():
    pm = (
        ProcessManagerDispatch(
            "Reserving", "reserving-pm", Rebuilder(factory=counter_pb2.CounterState)
        )
        .on_rejected(builders.FQ_RESERVE, lambda _n, _r, _s: None)
        .on_rejected("inventory:" + builders.FQ_RESERVE, lambda _n, _r, _s: None)
    )
    with Router() as router, pytest.raises(CodedError) as exc:
        router.register_process_manager(pm)

    assert exc.value.grpc == GrpcCode.INVALID_ARGUMENT


# --- lifecycle ---


def test_closing_twice_is_harmless_and_a_closed_router_refuses_dispatch():
    router = Router()
    router.register_aggregate(
        AggregateDispatch(
            "Ledger", "ledger", Rebuilder(factory=counter_pb2.CounterState)
        ).on_command(builders.FQ_INCREASE_BY, lambda _c, _s, _x: None)
    )
    router.close()
    router.close()

    with pytest.raises(TypeError):
        router.dispatch(builders.increase_command(1))
