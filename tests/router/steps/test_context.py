"""Facts and handler-context harness — runs the shared
``conformance/features/context.feature`` against the ledger aggregate, the
reserving process-manager and the tracking projector built through the
binding's hand-written APIs (``on_fact``, ``apply_with_context``,
``CommandContext.cover``, ``on_event_with_cover``,
``on_event_with_context``)."""

from __future__ import annotations

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

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
    pack,
)

from ..builders import (
    FQ_INCREASE_BY,
    FQ_INCREASED,
    cover_of,
    fq_from_url,
    root_of,
    type_url,
)
from ..gen.test.counter import counter_pb2
from . import CONFORMANCE_FEATURES

scenarios(str(CONFORMANCE_FEATURES / "context.feature"))


def _increased_page(seq: int | None = None):
    page = types_pb2.EventPage()
    if seq is not None:
        page.sequence = seq
    page.payload.CopyFrom(pack(counter_pb2.Increased()))
    return page


# --- the hand-built components ---


def _ledger_aggregate(covers: list, applied: list) -> AggregateDispatch:
    """The ledger aggregate (domain "ledger") over CounterState: Increased
    folds count += 1 and records the page sequence it applied; IncreaseBy records the
    handled cover and emits nothing; the only declared fact, Increased, is
    recorded as received and flagged by a CounterState carrying the count it
    brings the ledger to."""

    def apply_increased(state, _event, ctx: PageContext):
        state.count += 1
        applied.append(ctx.sequence)

    def increase_by(_cmd, _state, cctx):
        covers.append(cctx.cover)

    def record_increased(fact, state):
        return FactRecord(fact, [counter_pb2.CounterState(count=state.count + 1)])

    rebuilder = Rebuilder(factory=counter_pb2.CounterState).apply_with_context(
        FQ_INCREASED, apply_increased
    )
    return (
        AggregateDispatch("Ledger", "ledger", rebuilder)
        .on_command(FQ_INCREASE_BY, increase_by)
        .on_fact(FQ_INCREASED, record_increased)
    )


def _reserving_pm(covers: list) -> ProcessManagerDispatch:
    """The reserving process-manager (domain "reserving-pm", target
    "inventory") over CounterState (Increased folds count += 1): an Increased
    trigger from "counter" records
    the trigger cover and emits nothing."""

    def apply_increased(state, _event):
        state.count += 1

    def on_increased(_event, _state, trigger_cover):
        covers.append(trigger_cover)
        return process_manager_pb2.ProcessManagerHandleResponse()

    return (
        ProcessManagerDispatch(
            "Reserving",
            "reserving-pm",
            Rebuilder(factory=counter_pb2.CounterState).apply(
                FQ_INCREASED, apply_increased
            ),
            ["inventory"],
        )
    ).on_event_with_cover("counter", FQ_INCREASED, on_increased)


def _tracking_projector(pages: list) -> ProjectorDispatch:
    """The tracking projector: every Increased fold records its book's root
    and the page's sequence."""

    def fold(_state, _event, ctx: PageContext):
        root = ctx.cover.root.value if ctx.cover is not None else b""
        pages.append((root, ctx.sequence))

    return ProjectorDispatch("Tracker", counter_pb2.CounterState).on_event_with_context(
        FQ_INCREASED, fold
    )


# --- requests ---


def _ledger_command(label: str, sequences: list[int] | None = None):
    """An IncreaseBy(1) for the ledger root ``label``, over prior Increased
    events at ``sequences`` when given."""
    cc = types_pb2.ContextualCommand()
    cc.command.cover.CopyFrom(cover_of("ledger", label))
    cc.command.page.command.payload.CopyFrom(pack(counter_pb2.IncreaseBy(n=1)))
    if sequences:
        cc.events.pages.extend(_increased_page(seq) for seq in sequences)
        cc.events.next_sequence = sequences[-1] + 1
    return cc


def _ledger_fact(fact: str, prior: int):
    """A ``test.counter.<fact>`` fact (empty payload) for the ledger,
    delivered in the Fact arm over ``prior`` Increased events."""
    cc = _ledger_command("ledger-1")
    cc.command.page.ClearField("command")
    cc.command.page.fact.payload.type_url = type_url("test.counter." + fact)
    if prior:
        cc.events.pages.extend(_increased_page(i) for i in range(prior))
        cc.events.next_sequence = prior
    return cc


def _reserving_trigger(label: str, seq: int):
    req = process_manager_pb2.ProcessManagerHandleRequest()
    req.trigger.cover.CopyFrom(cover_of("counter", label))
    req.trigger.pages.append(_increased_page(seq))
    return req


def _tracked_book(label: str, sequences: list[int]):
    book = types_pb2.EventBook()
    book.cover.CopyFrom(cover_of("counter", label))
    for seq in sequences:
        book.pages.append(_increased_page(seq))
    return book


# --- world ---


class _World:
    def __init__(self):
        self.router = Router()
        self.covers: list = []
        self.applied: list = []
        self.pages: list = []
        self.fact = None
        self.refusal = None
        self.pm = None

    def close(self) -> None:
        self.router.close()


@pytest.fixture
def world():
    w = _World()
    yield w
    w.close()


@given("a ledger aggregate")
def _ledger(world):
    world.router.register_aggregate(_ledger_aggregate(world.covers, world.applied))


@given("a reserving process-manager")
def _reserving(world):
    world.router.register_process_manager(_reserving_pm(world.covers))


@given("a tracking projector")
def _tracking(world):
    world.router.register_projector(_tracking_projector(world.pages))


@when(
    parsers.re(
        r"an? (?P<fact>\w+) fact is handled over (?P<prior>\d+|no) prior (?:Increased )?events"
    )
)
def _fact_handled(world, fact, prior):
    request = _ledger_fact(fact, 0 if prior == "no" else int(prior))
    try:
        world.fact = world.router.dispatch(request)
    except CodedError as exc:
        world.refusal = exc


@then(
    parsers.re(
        r"the ledger applied Increased events at sequences (?P<first>\d+) and (?P<second>\d+)"
    )
)
def _applied_at(world, first, second):
    assert world.applied == [int(first), int(second)]


@when(
    parsers.re(
        r'an IncreaseBy command for ledger root "(?P<label>[^"]*)" is dispatched$'
    )
)
def _ledger_command_step(world, label):
    world.router.dispatch(_ledger_command(label))


@when(
    parsers.re(
        r'an IncreaseBy command for ledger root "(?P<label>[^"]*)" is dispatched '
        r"over Increased events at sequences (?P<first>\d+) and (?P<second>\d+)"
    )
)
def _ledger_command_over_step(world, label, first, second):
    world.router.dispatch(_ledger_command(label, [int(first), int(second)]))


@when(
    parsers.re(
        r'an Increased trigger of counter root "(?P<label>[^"]*)" at sequence (?P<seq>\d+) '
        r"is dispatched to the reserving process-manager"
    )
)
def _reserving_trigger_step(world, label, seq):
    world.pm = world.router.dispatch_process_manager(
        _reserving_trigger(label, int(seq))
    )


@when(
    parsers.re(
        r'Increased events of counter root "(?P<label>[^"]*)" at sequences (?P<first>\d+) '
        r"and (?P<second>\d+) are projected"
    )
)
def _projected(world, label, first, second):
    world.router.dispatch_projector(_tracked_book(label, [int(first), int(second)]))


@then(
    parsers.re(r"the Increased fact is recorded, flagged by the count (?P<count>\d+)")
)
def _recorded_and_flagged(world, count):
    assert world.refusal is None, f"fact refused: {world.refusal}"

    def recorded(page):
        name = fq_from_url(page.payload.type_url)
        if name == "test.counter.CounterState":
            return name, counter_pb2.CounterState.FromString(page.payload.value).count
        return name, None

    assert [recorded(p) for p in world.fact.events.pages] == [
        (FQ_INCREASED, None),
        ("test.counter.CounterState", int(count)),
    ]


@then(parsers.re(r"the fact is refused with (?P<code>[A-Z_]+) as INVALID_ARGUMENT"))
def _fact_refused(world, code):
    assert world.refusal is not None, "expected a refusal"
    assert world.refusal.code == code
    assert world.refusal.grpc == GrpcCode.INVALID_ARGUMENT


def _single_root(covers: list) -> bytes:
    assert len(covers) == 1, f"the handler ran {len(covers)} times, want once"
    assert covers[0] is not None, "the handler saw no cover"
    return covers[0].root.value


@then(parsers.re(r'the ledger handler saw root "(?P<label>[^"]*)"'))
def _ledger_saw(world, label):
    assert _single_root(world.covers) == root_of(label)


@then(parsers.re(r'the reserving process-manager saw trigger root "(?P<label>[^"]*)"'))
def _pm_saw(world, label):
    assert _single_root(world.covers) == root_of(label)


@then(
    parsers.re(
        r'the projector saw root "(?P<label>[^"]*)" at sequences (?P<first>\d+) and (?P<second>\d+)'
    )
)
def _projector_saw(world, label, first, second):
    root = root_of(label)
    assert world.pages == [(root, int(first)), (root, int(second))]
