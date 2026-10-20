"""What each host callback receives across the FFI (ABI 4) and what the
router hands back: the exact Any (type URL and payload bytes) every thunk
sees, the router-owned state every callback is handed (decoded into a fresh
message, folded in one batched call and re-encoded), the typed fold failure,
the page context each callback runs under, the registration descriptors and
hellos the router builds, registration failures, and router lifecycle."""

from __future__ import annotations

import pytest

from angzarr_client.proto.io.angzarr.v1 import process_manager_pb2, types_pb2
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
    page.sequence = seq
    page.payload.type_url = _INCREASED_URL
    page.payload.value = value
    return page


def _count_increases(state, _event):
    state.count += 1


def _counting_rebuilder() -> Rebuilder:
    return Rebuilder(factory=counter_pb2.CounterState).apply(
        builders.FQ_INCREASED, _count_increases
    )


def _ledger_command(n: int = 1, label: str = "ledger-1"):
    cc = types_pb2.ContextualCommand()
    cc.command.cover.CopyFrom(builders.cover_of("ledger", label))
    cc.command.page.command.payload.CopyFrom(pack(counter_pb2.IncreaseBy(n=n)))
    return cc


def _ledger_fact(label: str = "ledger-1"):
    cc = types_pb2.ContextualCommand()
    cc.command.cover.CopyFrom(builders.cover_of("ledger", label))
    cc.command.page.fact.payload.CopyFrom(_event_page(0).payload)
    return cc


def _noop(_cmd, _state, _cctx):
    return None


# --- the Any each thunk receives ---


def test_aggregate_thunks_receive_the_exact_type_url_and_payload():
    seen = []

    def apply_increased(state, event):
        seen.append(("apply", event.type_url, event.value))

    def handle(cmd, state, cctx):
        seen.append(("command", cmd.type_url, cmd.value))

    rebuilder = Rebuilder(factory=counter_pb2.CounterState).apply(
        builders.FQ_INCREASED, apply_increased
    )
    dispatch = AggregateDispatch("Ledger", "ledger", rebuilder).on_command(
        builders.FQ_INCREASE_BY, handle
    )
    cc = _ledger_command(3)
    cc.events.pages.append(_event_page(0))
    cc.events.next_sequence = 1
    with Router() as router:
        router.register_aggregate(dispatch)
        router.dispatch(cc)

    assert seen == [
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
            cc.command.page.command.payload.value = value
            router.dispatch(cc)

    assert seen == [b"\x01", b""]


def test_fact_handler_receives_the_fact_payload_and_its_record_is_answered():
    seen = []

    def observe(fact, state):
        seen.append((fact.type_url, fact.value))
        return FactRecord.as_received(fact)

    dispatch = AggregateDispatch("Ledger", "ledger", _counting_rebuilder()).on_fact(
        builders.FQ_INCREASED, observe
    )
    with Router() as router:
        router.register_aggregate(dispatch)
        resp = router.dispatch(_ledger_fact())

    assert seen == [(_INCREASED_URL, _PAYLOAD)]
    assert [p.payload.value for p in resp.events.pages] == [_PAYLOAD]


def test_projector_fold_receives_the_exact_type_url_and_payload():
    seen = []
    projector = ProjectorDispatch("Tracker", counter_pb2.CounterState).on_event(
        builders.FQ_INCREASED, lambda _s, e: seen.append((e.type_url, e.value))
    )
    book = types_pb2.EventBook()
    book.cover.domain = "counter"
    book.pages.append(_event_page(0))
    with Router() as router:
        router.register_projector(projector)
        router.dispatch_projector(book)

    assert seen == [(_INCREASED_URL, _PAYLOAD)]


def _saga_source(label: str = "order-1"):
    source = types_pb2.EventBook()
    source.cover.CopyFrom(builders.cover_of("order", label))
    source.pages.append(_event_page(4))
    return source


def _reserve():
    command = types_pb2.CommandBook()
    command.cover.domain = "inventory"
    command.page.command.payload.CopyFrom(pack(counter_pb2.Reserve()))
    return command


def test_saga_receives_the_exact_event_and_its_commands_are_kept():
    seen = []

    def translate(event, source_cover):
        seen.append((event.type_url, event.value, source_cover.root.value))
        return [_reserve()]

    saga = SagaDispatch("order-saga", "order").on_event(
        builders.FQ_INCREASED, translate
    )
    with Router() as router:
        router.register_saga(saga)
        resp = router.dispatch_saga(_saga_source())

    assert seen == [(_INCREASED_URL, _PAYLOAD, builders.root_of("order-1"))]
    assert list(resp.commands) == [_reserve()]


def test_saga_context_handler_sees_the_triggering_sequence():
    seen = []

    def translate(event, source: PageContext):
        seen.append(source.sequence)
        return []

    saga = SagaDispatch(
        "order-saga", "order", targets=["inventory"]
    ).on_event_with_context(builders.FQ_INCREASED, translate)
    with Router() as router:
        router.register_saga(saga)
        router.dispatch_saga(_saga_source())

    assert seen == [4]


@pytest.mark.parametrize("nothing", [[], None])
def test_a_saga_emitting_nothing_returns_an_empty_response(nothing):
    saga = SagaDispatch("order-saga", "order").on_event(
        builders.FQ_INCREASED, lambda _e, _c: nothing
    )
    with Router() as router:
        router.register_saga(saga)
        resp = router.dispatch_saga(_saga_source())

    assert resp.SerializeToString() == b""


def _pm_trigger(prior: int = 0):
    req = process_manager_pb2.ProcessManagerHandleRequest()
    req.trigger.cover.domain = "counter"
    req.trigger.pages.append(_event_page(2))
    for seq in range(prior):
        req.process_state.pages.append(_event_page(seq))
    return req


def test_process_manager_handler_receives_the_exact_event():
    seen = []

    def react(event, state):
        seen.append((event.type_url, event.value))
        return process_manager_pb2.ProcessManagerHandleResponse()

    pm = ProcessManagerDispatch(
        "Reserving", "reserving-pm", Rebuilder(factory=counter_pb2.CounterState)
    ).on_event("counter", builders.FQ_INCREASED, react)
    with Router() as router:
        router.register_process_manager(pm)
        router.dispatch_process_manager(_pm_trigger())

    assert seen == [(_INCREASED_URL, _PAYLOAD)]


# --- router-owned state ---


def test_every_callback_receives_a_fresh_copy_of_the_router_owned_state():
    # The command handler mutates the state it is handed; the router's state
    # is unchanged, so the fact handler after it sees the folded count only.
    seen = []

    def handle(_cmd, state, _cctx):
        seen.append(("command", state.count))
        state.count = 999

    def observe(fact, state):
        seen.append(("fact", state.count))
        return FactRecord.as_received(fact)

    dispatch = (
        AggregateDispatch("Ledger", "ledger", _counting_rebuilder())
        .on_command(builders.FQ_INCREASE_BY, handle)
        .on_fact(builders.FQ_INCREASED, observe)
    )
    with Router() as router:
        router.register_aggregate(dispatch)
        for cc in (_ledger_command(), _ledger_fact()):
            cc.events.pages.extend([_event_page(0), _event_page(1)])
            cc.events.next_sequence = 2
            router.dispatch(cc)

    assert seen == [("command", 2), ("fact", 2)]


def test_a_delivered_snapshot_is_the_initial_state_and_covers_its_pages():
    seen = []
    dispatch = AggregateDispatch("Ledger", "ledger", _counting_rebuilder()).on_command(
        builders.FQ_INCREASE_BY, lambda _c, state, _x: seen.append(state.count)
    )
    cc = _ledger_command()
    cc.events.snapshot.sequence = 1
    cc.events.snapshot.state.CopyFrom(pack(counter_pb2.CounterState(count=7)))
    cc.events.pages.extend([_event_page(1), _event_page(2)])
    cc.events.next_sequence = 3
    with Router() as router:
        router.register_aggregate(dispatch)
        router.dispatch(cc)

    assert seen == [8]


def test_the_fold_gets_every_uncovered_page_in_one_call_with_its_context():
    folds = []
    rebuilder = Rebuilder(factory=counter_pb2.CounterState).apply_with_context(
        builders.FQ_INCREASED,
        lambda state, _e, ctx: folds.append((ctx.cover.root.value, ctx.sequence)),
    )
    dispatch = AggregateDispatch("Ledger", "ledger", rebuilder).on_command(
        builders.FQ_INCREASE_BY, _noop
    )
    cc = _ledger_command()
    cc.events.cover.CopyFrom(builders.cover_of("ledger", "ledger-1"))
    unknown = types_pb2.EventPage(sequence=3)
    unknown.payload.CopyFrom(pack(counter_pb2.Reserve()))
    cc.events.pages.extend([_event_page(2), unknown, types_pb2.EventPage(sequence=4)])
    cc.events.pages.append(_event_page(5))
    cc.events.next_sequence = 6
    root = builders.root_of("ledger-1")
    with Router() as router:
        router.register_aggregate(dispatch)
        router.dispatch(cc)

    # Unknown types and pageless entries are skipped.
    assert folds == [(root, 2), (root, 5)]


def test_a_process_manager_handler_sees_its_rebuilt_state():
    seen = []

    def react(_event, state):
        seen.append(state.count)

    pm = ProcessManagerDispatch(
        "Reserving", "reserving-pm", _counting_rebuilder()
    ).on_event("counter", builders.FQ_INCREASED, react)
    with Router() as router:
        router.register_process_manager(pm)
        router.dispatch_process_manager(_pm_trigger(prior=3))

    assert seen == [3]


def test_a_failing_fold_is_internal_naming_the_failing_page():
    def apply_increased(state, event):
        if state.count == 1:
            raise ValueError("cannot fold a second increase")
        state.count += 1

    rebuilder = Rebuilder(factory=counter_pb2.CounterState).apply(
        builders.FQ_INCREASED, apply_increased
    )
    dispatch = AggregateDispatch("Ledger", "ledger", rebuilder).on_command(
        builders.FQ_INCREASE_BY, _noop
    )
    cc = _ledger_command()
    cc.events.pages.extend([_event_page(4), _event_page(5), _event_page(6)])
    cc.events.next_sequence = 7
    with Router() as router:
        router.register_aggregate(dispatch)
        with pytest.raises(CodedError) as exc:
            router.dispatch(cc)

    assert exc.value.code == "UNHANDLED_HANDLER_ERROR"
    assert exc.value.grpc == GrpcCode.INTERNAL
    assert exc.value.extras["sequence"] == "5"
    assert exc.value.extras["type_url"] == _INCREASED_URL
    assert exc.value.extras["state_type_url"] == "/test.counter.CounterState"
    assert exc.value.extras["fold_message"] == "cannot fold a second increase"


def test_a_failing_projector_fold_is_internal_naming_the_failing_page():
    def fold(_projection, _event):
        raise RuntimeError("projection broke")

    projector = ProjectorDispatch("Tracker", counter_pb2.CounterState).on_event(
        builders.FQ_INCREASED, fold
    )
    book = types_pb2.EventBook()
    book.cover.domain = "counter"
    book.pages.append(_event_page(9))
    with Router() as router:
        router.register_projector(projector)
        with pytest.raises(CodedError) as exc:
            router.dispatch_projector(book)

    assert exc.value.grpc == GrpcCode.INTERNAL
    assert exc.value.extras["sequence"] == "9"
    assert exc.value.extras["fold_message"] == "projection broke"


def test_a_state_that_is_not_a_protobuf_message_is_refused_at_registration():
    dispatch = AggregateDispatch("Ledger", "ledger", Rebuilder(factory=dict))
    with Router() as router, pytest.raises(TypeError, match="Ledger: state must be"):
        router.register_aggregate(dispatch)


# --- the router builds the Projection ---


def test_the_router_answers_the_projection_it_folded():
    projector = ProjectorDispatch("Tracker", counter_pb2.CounterState).on_event(
        builders.FQ_INCREASED, _count_increases
    )
    book = types_pb2.EventBook()
    book.cover.CopyFrom(builders.cover_of("counter", "counter-1"))
    book.pages.extend([_event_page(3), _event_page(4)])
    with Router() as router:
        router.register_projector(projector)
        proj = router.dispatch_projector(book)

    assert proj.projector == "Tracker"
    assert proj.cover == builders.cover_of("counter", "counter-1")
    assert proj.next_sequence == 5
    assert proj.projection == pack(counter_pb2.CounterState(count=2))


# --- rules version and registration hellos ---


def test_the_rules_version_is_reported_on_every_response():
    dispatch = (
        AggregateDispatch("Ledger", "ledger", _counting_rebuilder())
        .on_command(builders.FQ_INCREASE_BY, _noop)
        .with_rules_version("1.4.2+build.7")
    )
    pm = (
        ProcessManagerDispatch("Reserving", "reserving-pm", _counting_rebuilder())
        .on_event("counter", builders.FQ_INCREASED, lambda _e, _s: None)
        .with_rules_version("1.4.2+build.7")
    )
    with Router() as router:
        router.register_aggregate(dispatch)
        router.register_process_manager(pm)
        resp = router.dispatch(_ledger_command())
        pm_resp = router.dispatch_process_manager(_pm_trigger())

    assert resp.rules_version == "1.4.2+build.7"
    assert pm_resp.rules_version == "1.4.2+build.7"


def test_the_hellos_carry_every_component_in_registration_order():
    aggregate = (
        AggregateDispatch("Ledger", "ledger", _counting_rebuilder())
        .on_command(builders.FQ_INCREASE_BY, _noop)
        .with_rules_version("2.0.0")
    )
    saga = SagaDispatch("OrderSaga", "order", ["inventory"]).with_rules_version("2.0.0")
    pm = ProcessManagerDispatch(
        "Reserving", "reserving-pm", _counting_rebuilder()
    ).with_rules_version("2.0.0")
    pm.on_event("counter", builders.FQ_INCREASED, lambda _e, _s: None)
    projector = (
        ProjectorDispatch("Tracker", counter_pb2.CounterProjectorState)
        .for_domains("counter")
        .with_rules_version("2.0.0")
    )
    with Router() as router:
        router.register_aggregate(aggregate)
        router.register_saga(saga)
        router.register_process_manager(pm)
        router.register_projector(projector)
        hellos = router.component_descriptors()

    kinds = types_pb2.ComponentKind
    assert [(h.name, h.kind, [t.domain for t in h.inputs]) for h in hellos] == [
        ("Ledger", kinds.COMPONENT_KIND_AGGREGATE, []),
        ("OrderSaga", kinds.COMPONENT_KIND_SAGA, ["order"]),
        ("Reserving", kinds.COMPONENT_KIND_PROCESS_MANAGER, ["counter"]),
        ("Tracker", kinds.COMPONENT_KIND_PROJECTOR, ["counter"]),
    ]
    assert {h.rules_version for h in hellos} == {"2.0.0"}
    assert [h.state.type_url for h in hellos] == [
        "/test.counter.CounterState",
        "",
        "/test.counter.CounterState",
        "",
    ]
    resolutions = types_pb2.ConflictResolution
    assert list(hellos[0].state.options.conflict_resolutions) == [
        resolutions.CONFLICT_RESOLUTION_STRICT,
        resolutions.CONFLICT_RESOLUTION_AGGREGATE_HANDLES,
    ]


def test_a_hello_without_a_rules_version_is_refused_naming_the_component():
    with Router() as router:
        router.register_aggregate(
            AggregateDispatch("Ledger", "ledger", _counting_rebuilder())
        )
        with pytest.raises(CodedError) as exc:
            router.component_descriptors()

    assert exc.value.code == "MISSING_RULES_VERSION"
    assert exc.value.grpc == GrpcCode.INVALID_ARGUMENT
    assert exc.value.extras["component"] == "Ledger"


def test_a_router_with_nothing_registered_has_no_hellos():
    with Router() as router:
        assert router.component_descriptors() == []


# --- command upcasting ---


def test_a_command_upcaster_brings_an_old_payload_to_its_handler():
    seen = []

    def upcast(old):
        assert old.type_url == "/test.counter.FailHard"
        return counter_pb2.IncreaseBy(n=5)

    dispatch = (
        AggregateDispatch("Ledger", "ledger", _counting_rebuilder())
        .on_command(
            builders.FQ_INCREASE_BY,
            lambda cmd, _s, _c: seen.append(
                counter_pb2.IncreaseBy.FromString(cmd.value).n
            ),
        )
        .with_command_upcaster(builders.FQ_FAIL_HARD, upcast)
    )
    cc = _ledger_command()
    cc.command.page.command.payload.CopyFrom(pack(counter_pb2.FailHard()))
    with Router() as router:
        router.register_aggregate(dispatch)
        router.dispatch(cc)

    assert seen == [5]


def test_an_unknown_type_after_upcasting_names_both_type_urls():
    dispatch = (
        AggregateDispatch("Ledger", "ledger", _counting_rebuilder())
        .on_command(builders.FQ_INCREASE_BY, _noop)
        .with_command_upcaster(
            builders.FQ_FAIL_HARD, lambda _old: counter_pb2.Reserve()
        )
    )
    cc = _ledger_command()
    cc.command.page.command.payload.CopyFrom(pack(counter_pb2.FailHard()))
    with Router() as router:
        router.register_aggregate(dispatch)
        with pytest.raises(CodedError) as exc:
            router.dispatch(cc)

    assert exc.value.grpc == GrpcCode.INVALID_ARGUMENT
    assert exc.value.message == "no handler for type URL /test.counter.FailHard"
    assert exc.value.extras["upcast_type_url"] == "/test.counter.Reserve"


def test_an_upcaster_returning_none_leaves_the_payload_unchanged():
    dispatch = (
        AggregateDispatch("Ledger", "ledger", _counting_rebuilder())
        .on_command(builders.FQ_INCREASE_BY, _noop)
        .with_command_upcaster(builders.FQ_FAIL_HARD, lambda _old: None)
    )
    cc = _ledger_command()
    cc.command.page.command.payload.CopyFrom(pack(counter_pb2.FailHard()))
    with Router() as router:
        router.register_aggregate(dispatch)
        with pytest.raises(CodedError) as exc:
            router.dispatch(cc)

    assert exc.value.extras["upcast_type_url"] == "/test.counter.FailHard"


def test_an_upcaster_source_type_claimed_twice_is_refused():
    def ledger(name, source):
        return AggregateDispatch(
            name, "ledger", _counting_rebuilder()
        ).with_command_upcaster(source, lambda old: old)

    with Router() as router:
        router.register_aggregate(ledger("Ledger", builders.FQ_FAIL_HARD))
        with pytest.raises(CodedError) as exc:
            router.register_aggregate(ledger("Shadow", builders.FQ_FAIL_HARD))

    assert exc.value.grpc == GrpcCode.INVALID_ARGUMENT


# --- conflict resolution across the ABI ---


def test_an_aggregate_handles_command_that_does_not_resolve_is_aborted():
    # The handler returns events but never resolves the window: the
    # CommandOutput carries no resolved nonce, so the router discards it.
    dispatch = AggregateDispatch("Ledger", "ledger", _counting_rebuilder()).on_command(
        builders.FQ_INCREASE_BY, lambda _c, _s, _x: types_pb2.EventBook()
    )
    cc = _ledger_command()
    cc.command.handling.expected_sequence = 0
    cc.command.handling.conflict_resolution = (
        types_pb2.ConflictResolution.CONFLICT_RESOLUTION_AGGREGATE_HANDLES
    )
    cc.conflict_nonce = b"nonce-1"
    cc.events.pages.append(_event_page(0))
    cc.events.next_sequence = 1
    with Router() as router:
        router.register_aggregate(dispatch)
        with pytest.raises(CodedError) as exc:
            router.dispatch(cc)

    assert exc.value.code == "CONFLICT_UNRESOLVED"
    assert exc.value.grpc == GrpcCode.ABORTED


def test_commutative_without_a_changed_callback_is_unsupported():
    dispatch = AggregateDispatch("Ledger", "ledger", _counting_rebuilder()).on_command(
        builders.FQ_INCREASE_BY, _noop
    )
    cc = _ledger_command()
    cc.command.handling.conflict_resolution = (
        types_pb2.ConflictResolution.CONFLICT_RESOLUTION_COMMUTATIVE
    )
    with Router() as router:
        router.register_aggregate(dispatch)
        with pytest.raises(CodedError) as exc:
            router.dispatch(cc)

    assert exc.value.code == "CONFLICT_RESOLUTION_UNSUPPORTED"
    assert exc.value.grpc == GrpcCode.INVALID_ARGUMENT


# --- fact handler failures ---


def test_a_fact_handler_returning_a_non_record_names_the_type_in_its_failure():
    dispatch = AggregateDispatch("Ledger", "ledger", _counting_rebuilder()).on_fact(
        builders.FQ_INCREASED, lambda fact, _state: fact
    )
    with Router() as router:
        router.register_aggregate(dispatch)
        with pytest.raises(CodedError) as exc:
            router.dispatch(_ledger_fact())

    assert exc.value.code == "UNHANDLED_HANDLER_ERROR"
    assert _INCREASED_URL in exc.value.message
    assert "not a FactRecord" in exc.value.message


# --- the dispatch-level page context ---


def test_fact_handler_sees_the_command_cover():
    seen = []

    def observe(fact, state):
        seen.append(current_cover().root.value)
        return FactRecord.as_received(fact)

    dispatch = AggregateDispatch("Ledger", "ledger", _counting_rebuilder()).on_fact(
        builders.FQ_INCREASED, observe
    )
    with Router() as router:
        router.register_aggregate(dispatch)
        router.dispatch(_ledger_fact("facts-1"))

    assert seen == [builders.root_of("facts-1")]


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
    cc.command.page.command.payload.CopyFrom(pack(counter_pb2.IncreaseBy(n=1)))
    with Router() as router:
        router.register_aggregate(dispatch)
        router.dispatch(cc)

    assert seen == [(None, None)]


def test_current_page_outside_a_dispatch_names_the_problem():
    with pytest.raises(
        RuntimeError, match="^no angzarr dispatch is being handled on this thread$"
    ):
        current_page()


# --- registration failures ---


def test_a_second_projector_is_refused_with_invalid_argument():
    with Router() as router:
        router.register_projector(
            ProjectorDispatch("Tracker", counter_pb2.CounterState)
        )
        with pytest.raises(CodedError) as exc:
            router.register_projector(
                ProjectorDispatch("Other", counter_pb2.CounterState)
            )

    assert exc.value.grpc == GrpcCode.INVALID_ARGUMENT


def test_a_second_claim_of_a_command_type_is_refused_with_invalid_argument():
    def ledger(name):
        return AggregateDispatch(
            name, "ledger", Rebuilder(factory=counter_pb2.CounterState)
        ).on_command(builders.FQ_INCREASE_BY, _noop)

    with Router() as router:
        router.register_aggregate(ledger("Ledger"))
        with pytest.raises(CodedError) as exc:
            router.register_aggregate(ledger("Shadow"))

    assert exc.value.grpc == GrpcCode.INVALID_ARGUMENT


# --- lifecycle ---


def test_closing_twice_is_harmless_and_a_closed_router_refuses_dispatch():
    router = Router()
    router.register_aggregate(
        AggregateDispatch(
            "Ledger", "ledger", Rebuilder(factory=counter_pb2.CounterState)
        ).on_command(builders.FQ_INCREASE_BY, _noop)
    )
    router.close()
    router.close()

    with pytest.raises(TypeError):
        router.dispatch(builders.increase_command(1))


def test_closing_a_router_drops_only_its_own_callbacks():
    from angzarr_client.router import _dispatch

    seen = []

    def ledger():
        return AggregateDispatch("Ledger", "ledger", _counting_rebuilder()).on_command(
            builders.FQ_INCREASE_BY, lambda _c, s, _x: seen.append(s.count)
        )

    before = set(_dispatch._CALLBACKS)
    kept, closed = Router(), Router()
    kept.register_aggregate(ledger())
    closed.register_aggregate(ledger())
    closed.close()
    try:
        kept.dispatch(_ledger_command())
        assert seen == [0]
        assert len(set(_dispatch._CALLBACKS) - before) == 2
    finally:
        kept.close()
    assert set(_dispatch._CALLBACKS) == before


# --- the post-command snapshot and the PM's process state ---


def _emitting(n: int):
    def handle(_cmd, _state, _cctx):
        book = types_pb2.EventBook()
        for _ in range(n):
            book.pages.add().payload.CopyFrom(pack(counter_pb2.Increased()))
        return book if n else None

    return handle


@pytest.mark.parametrize(("emitted", "count"), [(2, 3), (0, 1)])
def test_c0570_every_answer_carries_the_state_after_the_command(emitted, count):
    dispatch = AggregateDispatch("Ledger", "ledger", _counting_rebuilder()).on_command(
        builders.FQ_INCREASE_BY, _emitting(emitted)
    )
    cc = _ledger_command()
    cc.events.pages.append(_event_page(0))
    cc.events.next_sequence = 1
    with Router() as router:
        router.register_aggregate(dispatch)
        resp = router.dispatch(cc)

    assert resp.events.snapshot.state == pack(counter_pb2.CounterState(count=count))
    assert resp.events.snapshot.sequence == 0, "the coordinator stamps it"


def test_c0570_a_fact_answer_carries_the_state_after_its_record():
    dispatch = AggregateDispatch("Ledger", "ledger", _counting_rebuilder()).on_fact(
        builders.FQ_INCREASED, lambda fact, _s: FactRecord.as_received(fact)
    )
    with Router() as router:
        router.register_aggregate(dispatch)
        resp = router.dispatch(_ledger_fact())

    assert resp.events.snapshot.state == pack(counter_pb2.CounterState(count=1))


@pytest.mark.parametrize("writes", [True, False])
def test_c0571_a_pm_answer_carries_its_state_after_the_reaction(writes):
    def react(_event, _state):
        resp = process_manager_pb2.ProcessManagerHandleResponse()
        if writes:
            resp.process_events.add().pages.add().payload.CopyFrom(
                pack(counter_pb2.Increased())
            )
        return resp

    pm = ProcessManagerDispatch(
        "Reserving", "reserving-pm", _counting_rebuilder()
    ).on_event("counter", builders.FQ_INCREASED, react)
    with Router() as router:
        router.register_process_manager(pm)
        resp = router.dispatch_process_manager(_pm_trigger(prior=2))

    expected = 3 if writes else 2
    assert resp.process_state.state == pack(counter_pb2.CounterState(count=expected))
    assert all(not book.HasField("snapshot") for book in resp.process_events)
