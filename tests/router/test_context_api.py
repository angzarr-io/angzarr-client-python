"""The hand-written registration APIs for facts and handler context: the
cover a handler is handling (CommandContext.cover, the PM trigger cover, the
projector page context, and the ``current_cover``/``current_page`` accessors
generated handlers reach it through) and fact handlers."""

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


def _ledger_rebuilder() -> Rebuilder:
    def apply_increased(state, _event):
        state.count += 1

    return Rebuilder(factory=counter_pb2.CounterState).apply(
        builders.FQ_INCREASED, apply_increased
    )


def _increased_page(seq: int):
    page = types_pb2.EventPage()
    page.sequence = seq
    page.payload.CopyFrom(pack(counter_pb2.Increased()))
    return page


def _ledger_command(label: str):
    cc = types_pb2.ContextualCommand()
    cc.command.cover.CopyFrom(builders.cover_of("ledger", label))
    cc.command.page.command.payload.CopyFrom(pack(counter_pb2.IncreaseBy(n=1)))
    return cc


# --- CommandContext.cover and current_cover() ---


def test_command_handler_reads_its_cover_from_the_context_and_the_accessor():
    seen = []

    def handle(cmd, state, cctx):
        seen.append(
            (cctx.cover.root.value, current_cover().root.value, current_page().sequence)
        )

    dispatch = AggregateDispatch("Ledger", "ledger", _ledger_rebuilder())
    dispatch.on_command(builders.FQ_INCREASE_BY, handle)
    with Router() as router:
        router.register_aggregate(dispatch)
        resp = router.dispatch(_ledger_command("ledger-1"))

    root = builders.root_of("ledger-1")
    assert seen == [(root, root, 0)]
    assert len(resp.events.pages) == 0


def test_current_cover_outside_a_handler_raises():
    with pytest.raises(RuntimeError):
        current_cover()
    with pytest.raises(RuntimeError):
        current_page()


# --- facts ---


def _fact_command(fq: str, prior: int):
    """A ``fq`` fact for the ledger over ``prior`` Increased events."""
    cc = types_pb2.ContextualCommand()
    cc.command.cover.domain = "ledger"
    cc.command.page.fact.payload.type_url = builders.type_url(fq)
    for i in range(prior):
        cc.events.pages.append(_increased_page(i))
    cc.events.next_sequence = prior
    return cc


def _recorded(book) -> list:
    def one(page):
        name = builders.fq_from_url(page.payload.type_url)
        if name == "test.counter.CounterState":
            return name, counter_pb2.CounterState.FromString(page.payload.value).count
        return name, None

    return [one(p) for p in book.pages]


def _handle_fact(handler, cc):
    dispatch = AggregateDispatch("Ledger", "ledger", _ledger_rebuilder())
    dispatch.on_fact(builders.FQ_INCREASED, handler)
    with Router() as router:
        router.register_aggregate(dispatch)
        return router.dispatch(cc).events


def test_a_fact_record_annotates_the_fact_and_flags_it_with_messages_or_anys():
    def annotate_with_messages(fact, state):
        return FactRecord(
            counter_pb2.CounterState(count=state.count),
            [counter_pb2.Increased(), counter_pb2.CounterState(count=state.count + 10)],
        )

    def annotate_with_anys(fact, state):
        return FactRecord(
            pack(counter_pb2.CounterState(count=state.count)),
            (
                pack(counter_pb2.Increased()),
                pack(counter_pb2.CounterState(count=state.count + 10)),
            ),
        )

    for handler in (annotate_with_messages, annotate_with_anys):
        book = _handle_fact(handler, _fact_command(builders.FQ_INCREASED, 3))

        assert _recorded(book) == [
            ("test.counter.CounterState", 3),
            (builders.FQ_INCREASED, None),
            ("test.counter.CounterState", 13),
        ]
        assert not book.pages[1].HasField("meta"), "a flag carries no meta"


def test_a_fact_record_as_received_records_the_fact_with_no_flags():
    seen = []

    def observe(fact, state):
        seen.append(builders.fq_from_url(fact.type_url))
        return FactRecord.as_received(fact)

    book = _handle_fact(observe, _fact_command(builders.FQ_INCREASED, 0))

    assert seen == [builders.FQ_INCREASED]
    assert _recorded(book) == [(builders.FQ_INCREASED, None)]
    assert FactRecord.as_received(pack(counter_pb2.Increased())).flags == ()


def test_a_fact_handler_returning_anything_but_a_fact_record_is_an_unhandled_error():
    def bare_any(fact, state):
        return fact

    with pytest.raises(CodedError) as exc:
        _handle_fact(bare_any, _fact_command(builders.FQ_INCREASED, 0))

    assert exc.value.code == "UNHANDLED_HANDLER_ERROR"
    assert exc.value.grpc == GrpcCode.INTERNAL


def test_an_undeclared_fact_is_refused_before_any_fact_handler_runs():
    seen = []

    def observe(fact, state):
        seen.append(fact.type_url)
        return FactRecord.as_received(fact)

    with pytest.raises(CodedError) as exc:
        _handle_fact(observe, _fact_command(builders.FQ_RESERVE, 2))

    assert exc.value.code == "NO_FACT_HANDLER"
    assert exc.value.grpc == GrpcCode.INVALID_ARGUMENT
    assert seen == []


# --- applier page context ---


def test_appliers_read_each_event_cover_and_sequence_by_argument_and_accessor():
    seen = []

    def apply_rich(state, _event, ctx: PageContext):
        seen.append(("rich", ctx.cover.root.value, ctx.sequence))

    def apply_plain(state, _event):
        page = current_page()
        seen.append(("plain", page.cover.root.value, page.sequence))

    rebuilder = (
        Rebuilder(factory=counter_pb2.CounterState)
        .apply_with_context(builders.FQ_INCREASED, apply_rich)
        .apply("test.counter.Reserve", apply_plain)
    )
    dispatch = AggregateDispatch("Ledger", "ledger", rebuilder)
    dispatch.on_command(builders.FQ_INCREASE_BY, lambda _cmd, _state, _cctx: None)
    cc = _ledger_command("ledger-1")
    cc.events.cover.CopyFrom(builders.cover_of("ledger", "history-1"))
    cc.events.pages.append(_increased_page(4))
    reserve = cc.events.pages.add()
    reserve.sequence = 5
    reserve.payload.CopyFrom(pack(counter_pb2.Reserve()))
    cc.events.next_sequence = 6
    with Router() as router:
        router.register_aggregate(dispatch)
        router.dispatch(cc)

    history = builders.root_of("history-1")
    assert seen == [("rich", history, 4), ("plain", history, 5)]


# --- process-manager trigger cover ---


def _pm_trigger(label: str, seq: int):
    req = process_manager_pb2.ProcessManagerHandleRequest()
    req.trigger.cover.CopyFrom(builders.cover_of("counter", label))
    req.trigger.pages.append(_increased_page(seq))
    return req


def test_process_manager_handler_reads_the_trigger_cover():
    seen = []

    def with_cover(event, state, trigger_cover):
        seen.append(("rich", trigger_cover.root.value))
        return process_manager_pb2.ProcessManagerHandleResponse()

    def plain(event, state):
        seen.append(("accessor", current_cover().root.value))
        return process_manager_pb2.ProcessManagerHandleResponse()

    root = builders.root_of("table-1")
    for register, kind in (
        (
            lambda pm: pm.on_event_with_cover(
                "counter", builders.FQ_INCREASED, with_cover
            ),
            "rich",
        ),
        (lambda pm: pm.on_event("counter", builders.FQ_INCREASED, plain), "accessor"),
    ):
        seen.clear()
        pm = ProcessManagerDispatch(
            "Reserving",
            "reserving-pm",
            Rebuilder(factory=counter_pb2.CounterState),
            ["inventory"],
        )
        register(pm)
        with Router() as router:
            router.register_process_manager(pm)
            router.dispatch_process_manager(_pm_trigger("table-1", 2))
        assert seen == [(kind, root)]


# --- saga source cover through the accessor ---


def test_saga_handler_reaches_the_source_cover_through_the_accessor():
    seen = []

    def translate(event, source_cover):
        seen.append((source_cover.root.value, current_cover().root.value))
        return []

    saga = SagaDispatch("order-saga", "order", "inventory").on_event(
        builders.FQ_INCREASED, translate
    )
    source = types_pb2.EventBook()
    source.cover.CopyFrom(builders.cover_of("order", "order-1"))
    source.pages.append(_increased_page(0))
    with Router() as router:
        router.register_saga(saga)
        router.dispatch_saga(source)

    root = builders.root_of("order-1")
    assert seen == [(root, root)]


def test_saga_handlers_read_the_source_cover_and_sequence_by_argument_and_accessor():
    seen = []

    def translate_rich(event, source: PageContext):
        seen.append(("rich", source.cover.root.value, source.sequence))
        return []

    def translate_plain(event, source_cover):
        page = current_page()
        seen.append(("plain", page.cover.root.value, page.sequence))
        return []

    saga = (
        SagaDispatch("order-saga", "order", "inventory")
        .on_event_with_context(builders.FQ_INCREASED, translate_rich)
        .on_event("test.counter.Reserve", translate_plain)
    )
    rich = types_pb2.EventBook()
    rich.cover.CopyFrom(builders.cover_of("order", "order-1"))
    rich.pages.append(_increased_page(7))
    plain = types_pb2.EventBook()
    plain.cover.CopyFrom(builders.cover_of("order", "order-2"))
    reserve = plain.pages.add()
    reserve.sequence = 9
    reserve.payload.CopyFrom(pack(counter_pb2.Reserve()))
    with Router() as router:
        router.register_saga(saga)
        router.dispatch_saga(rich)
        router.dispatch_saga(plain)

    assert seen == [
        ("rich", builders.root_of("order-1"), 7),
        ("plain", builders.root_of("order-2"), 9),
    ]


# --- projector page context ---


def _tracked_book(label: str, sequences: list[int]):
    book = types_pb2.EventBook()
    book.cover.CopyFrom(builders.cover_of("counter", label))
    for seq in sequences:
        book.pages.append(_increased_page(seq))
    return book


def test_projector_fold_reads_each_event_root_and_sequence():
    root = builders.root_of("counter-1")
    rich: list = []
    accessor: list = []

    def fold_rich(state, event, ctx: PageContext):
        rich.append((ctx.cover.root.value, ctx.sequence))

    def fold_plain(state, event):
        accessor.append((current_cover().root.value, current_page().sequence))

    for register in (
        lambda p: p.on_event_with_context(builders.FQ_INCREASED, fold_rich),
        lambda p: p.on_event(builders.FQ_INCREASED, fold_plain),
    ):
        projector = ProjectorDispatch("Tracker", counter_pb2.CounterState)
        register(projector)
        with Router() as router:
            router.register_projector(projector)
            router.dispatch_projector(_tracked_book("counter-1", [4, 5]))

    assert rich == [(root, 4), (root, 5)]
    assert accessor == [(root, 4), (root, 5)]
