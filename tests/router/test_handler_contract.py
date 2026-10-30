"""The router's complete contract (decision 14.42): angzarr-project's
handler-contract tier (features/handler-contract/), run in place against
this package's native router, over the tier's fixtures (fixture/, the
messages generated into tests/router/gen by ``just contract-gen``).

The business code is tests/router/contract.py. The steps play the
coordinator: they build each delivery well formed and read the router's
answer, or the coded error it refuses with, as the wire would carry it.
"""

from __future__ import annotations

import re

import pytest
from google.protobuf import any_pb2, timestamp_pb2
from google.rpc import error_details_pb2
from pytest_bdd import given, parsers, scenario, then, when

from angzarr_client.proto.io.angzarr.v1 import policy_pb2, types_pb2
from angzarr_client.router import (
    WILDCARD_DOMAIN,
    AggregateDispatch,
    Bug,
    CodedError,
    GrpcCode,
    Immediate,
    PolicyDispatch,
    ProcessManagerDispatch,
    ProjectorDispatch,
    Rebuilder,
    Refused,
    Rejection,
    RetryableBug,
    Router,
    UnclassifiedError,
    pack,
)

from . import contract as fx
from .gen import counter_pb2, order_pb2


@scenario(
    str(fx.FIXTURE.parent / "counter_dispatch.feature"),
    "Increasing a new counter records that many events",
)
def test_c0693_increasing_a_new_counter_records_that_many_events():
    pass


_QUOTED = re.compile(r'"([^"]*)"')


def _quoted(text: str) -> list[str]:
    return _QUOTED.findall(text)


class World:
    """One scenario: the business binary's version and component, what its
    business code saw, the delivery built, and the router's answer."""

    def __init__(self) -> None:
        self.version = ""
        self.component = None
        self.seen: list[fx.Observed] = []
        self.behave = None
        self.snapshot: types_pb2.Snapshot | None = None
        self.pages: list[types_pb2.EventPage] = []
        self.recorded_at: timestamp_pb2.Timestamp | None = None
        self.router: Router | None = None
        self.answer = None
        self.err: CodedError | None = None
        self.sent = None
        self.events: types_pb2.EventBook | None = None
        self.pm_version = ""

    def hosting(self, component) -> Router:
        """The router, hosting ``component`` unless one is hosted."""
        if self.router is None:
            self.router = Router()
            self.router.register_aggregate(component)
            self.component = component
        return self.router

    def order(self) -> Router:
        return self.hosting(
            self.component or fx.order_aggregate(self.version, self.seen, self.behave)
        )

    def answer_of(self, call, request) -> None:
        self.sent = request
        try:
            self.answer, self.err = call(request), None
        except CodedError as exc:
            self.answer, self.err = None, exc

    def info(self) -> error_details_pb2.ErrorInfo:
        assert self.err is not None, f"answered, not refused: {self.answer}"
        info = error_details_pb2.ErrorInfo()
        assert self.err.status().details[0].Unpack(info)
        return info

    def last(self) -> fx.Observed:
        assert self.err is None, f"refused: {self.err}"
        assert self.seen, "business code saw nothing"
        return self.seen[-1]


@pytest.fixture
def world():
    return World()


# ===========================================================================
# counter_dispatch.feature
# ===========================================================================


def _increased_pages(n: int, prefix: str | None = None, start: int = 0) -> list:
    pages = []
    for seq in range(start, start + n):
        page = fx.txtpb("event_increased.txtpb", types_pb2.EventPage())
        page.sequence = seq
        if prefix is not None:
            page.payload.type_url = prefix + fx.fq(counter_pb2.Increased)
        pages.append(page)
    return pages


def _counter_book(pages, snapshot=None) -> types_pb2.EventBook:
    book = types_pb2.EventBook(cover=fx.cover_of("counter", "counter-1"), pages=pages)
    if snapshot is not None:
        book.snapshot.CopyFrom(snapshot)
    book.next_sequence = pages[-1].sequence + 1 if pages else 0
    return book


@given("a new counter")
def _new_counter(world):
    world.hosting(fx.counter_aggregate(world.seen))
    world.events = None


@given(parsers.re(r"a counter that has already recorded (?P<n>\d+) increases?$"))
def _counter_recorded(world, n):
    _new_counter(world)
    world.events = _counter_book(_increased_pages(int(n)))


@given(
    parsers.re(
        r'a counter that has already recorded (?P<n>\d+) increases? under the "(?P<prefix>[^"]*)" type-URL prefix'
    )
)
def _counter_recorded_prefixed(world, n, prefix):
    _new_counter(world)
    world.events = _counter_book(_increased_pages(int(n), prefix))


@given("a counter whose history holds a corrupt event")
def _counter_corrupt(world):
    _new_counter(world)
    pages = _increased_pages(1)
    pages[0].payload.value = b"\xff\xff\xff"
    world.events = _counter_book(pages)


@given(
    parsers.re(
        r"a counter delivered a starting state of (?P<count>\d+) at sequence (?P<seq>\d+) "
        r"and one Increased event at sequence (?P<next>\d+)"
    )
)
def _counter_from_snapshot(world, count, seq, next):
    _new_counter(world)
    snapshot = types_pb2.Snapshot(
        sequence=int(seq), state=pack(counter_pb2.CounterState(count=int(count)))
    )
    world.events = _counter_book(_increased_pages(1, start=int(next)), snapshot)


def _dispatch_counter(world, request) -> None:
    if world.events is not None:
        request.events.CopyFrom(world.events)
    world.answer_of(world.router.dispatch, request)


def _increase(n: int, prefix: str | None = None) -> types_pb2.ContextualCommand:
    request = fx.txtpb("command_increase.txtpb", types_pb2.ContextualCommand())
    payload = request.command.page.command.payload
    payload.value = counter_pb2.IncreaseBy(n=n).SerializeToString()
    if prefix is not None:
        payload.type_url = prefix + fx.fq(counter_pb2.IncreaseBy)
    return request


@when(parsers.re(r"the operator increases the counter by (?P<n>\d+)$"))
def _operator_increases(world, n):
    _dispatch_counter(world, _increase(int(n)))


@when(
    parsers.re(
        r'the operator increases the counter by (?P<n>\d+) under the "(?P<prefix>[^"]*)" type-URL prefix'
    )
)
def _operator_increases_prefixed(world, n, prefix):
    _dispatch_counter(world, _increase(int(n), prefix))


@when("the operator triggers a hard failure")
def _hard_failure(world):
    _dispatch_counter(
        world, fx.txtpb("command_failhard.txtpb", types_pb2.ContextualCommand())
    )


@when("an unhandled command is dispatched")
def _unhandled(world):
    _dispatch_counter(
        world, fx.txtpb("command_unhandled.txtpb", types_pb2.ContextualCommand())
    )


@then(parsers.re(r"(?P<n>\d+) increases are recorded"))
def _increases_recorded(world, n):
    assert world.err is None, f"refused: {world.err}"
    names = [fx.fq_of(p.payload) for p in world.answer.events.pages]
    assert names == [fx.fq(counter_pb2.Increased)] * int(n)


@then("the recorded events carry no router-stamped sequence")
def _unstamped(world):
    for page in world.answer.events.pages:
        assert page.sequence == 0
        assert not page.HasField("meta")


@then(parsers.re(r"the command (?:is rejected as|fails with) (?P<code>[A-Z_]+)$"))
def _fails_with(world, code):
    assert world.err is not None, f"answered, not refused: {world.answer}"
    assert world.err.code == code


@then("no events are recorded")
def _no_events(world):
    assert world.answer is None


@then(parsers.re(r'the refusal is INTERNAL naming the type URL "(?P<url>[^"]*)"'))
def _refusal_names(world, url):
    assert world.err.grpc == GrpcCode.INTERNAL
    assert dict(world.info().metadata) == {"type_url": url}


@then(
    parsers.re(
        r"the handler saw (?P<prior>no prior|prior) history, at next sequence (?P<seq>\d+)"
    )
)
def _handler_saw_history(world, prior, seq):
    ctx = world.last().ctx
    assert ctx.had_prior_events is (prior == "prior")
    assert ctx.next_sequence == int(seq)


@then(
    parsers.re(
        r"the handler saw a counter of (?P<count>\d+), at next sequence (?P<seq>\d+)"
    )
)
def _handler_saw_counter(world, count, seq):
    seen = world.last()
    assert seen.count == int(count)
    assert seen.ctx.next_sequence == int(seq)


# ===========================================================================
# context.feature
# ===========================================================================


@given("a ledger aggregate")
def _ledger(world):
    world.hosting(fx.ledger_aggregate(world.seen))


@given("a tracking projector")
def _tracker(world):
    world.router = Router()
    world.router.register_projector(fx.counter_projector(world.seen, WILDCARD_DOMAIN))


def _ledger_command(label: str, sequences=()) -> types_pb2.ContextualCommand:
    request = types_pb2.ContextualCommand()
    request.command.cover.CopyFrom(fx.cover_of("ledger", label))
    request.command.page.command.payload.CopyFrom(pack(counter_pb2.IncreaseBy(n=1)))
    if sequences:
        request.events.pages.extend(
            fx.page_of(counter_pb2.Increased(), s) for s in sequences
        )
        request.events.next_sequence = sequences[-1] + 1
    return request


@when(
    parsers.re(
        r'an IncreaseBy command for ledger root "(?P<label>[^"]*)" is dispatched'
        r"(?: over Increased events at sequences (?P<first>\d+) and (?P<second>\d+))?$"
    )
)
def _ledger_dispatched(world, label, first, second):
    sequences = [int(first), int(second)] if first is not None else []
    world.answer_of(world.router.dispatch, _ledger_command(label, sequences))


@then(
    parsers.re(
        r"the ledger applied Increased events at sequences (?P<a>\d+) and (?P<b>\d+), "
        r"then its emitted event at (?P<c>\d+)"
    )
)
def _ledger_applied(world, a, b, c):
    assert world.err is None, f"refused: {world.err}"
    applied = [s.sequence for s in world.seen if s.sequence is not None]
    assert applied == [int(a), int(b), int(c)]


@then(parsers.re(r'the ledger handler saw root "(?P<label>[^"]*)"'))
def _ledger_saw(world, label):
    covers = [s.cover for s in world.seen if s.cover is not None]
    assert [c.root.value for c in covers] == [fx.root_of(label)]


@then(
    parsers.re(
        r"the (?P<name>\w+) fact is recorded, flagged by the count (?P<count>\d+)"
    )
)
def _fact_flagged(world, name, count):
    assert world.err is None, f"refused: {world.err}"
    pages = world.answer.events.pages
    assert [fx.fq_of(p.payload) for p in pages] == [
        "test.counter." + name,
        fx.fq(counter_pb2.CounterState),
    ]
    assert counter_pb2.CounterState.FromString(pages[1].payload.value).count == int(
        count
    )


@then(parsers.re(r"the fact is refused with (?P<code>[A-Z_]+) as (?P<status>[A-Z_]+)"))
def _fact_refused(world, code, status):
    assert world.err is not None, f"answered, not refused: {world.answer}"
    assert (world.err.code, world.err.grpc) == (code, GrpcCode[status])
    assert not world.seen


@when(
    parsers.re(
        r'Increased events of counter root "(?P<label>[^"]*)" at sequences (?P<a>\d+) '
        r"and (?P<b>\d+) are projected"
    )
)
def _projected(world, label, a, b):
    book = types_pb2.EventBook(cover=fx.cover_of("counter", label))
    book.pages.extend(fx.page_of(counter_pb2.Increased(), int(s)) for s in (a, b))
    world.answer_of(world.router.dispatch_projector, book)


@then(
    parsers.re(
        r'the projector saw root "(?P<label>[^"]*)" at sequences (?P<a>\d+) and (?P<b>\d+)'
    )
)
def _projector_saw(world, label, a, b):
    root = fx.root_of(label)
    assert [(s.cover, s.sequence) for s in world.seen] == [
        (root, int(a)),
        (root, int(b)),
    ]


# ===========================================================================
# policy_dispatch.feature
# ===========================================================================


@given(parsers.re(r'an order policy delivering to "(?P<domain>[^"]*)"'))
def _order_policy(world, domain):
    world.router = Router()
    world.router.register_policy(fx.order_policy(domain, world.seen))


@when(
    parsers.re(r"an? (?P<event>\w+) event(?: at sequence (?P<seq>\d+))? is dispatched$")
)
def _event_dispatched(world, event, seq):
    book = types_pb2.EventBook(cover=fx.cover_of("order", "order-1"))
    page = book.pages.add(sequence=int(seq or 0))
    page.payload.type_url = "/test.counter." + event
    world.answer_of(world.router.dispatch_policy, book)


@then(
    parsers.re(
        r'the policy emits one command to "(?P<domain>[^"]*)", in the reaction to sequence (?P<seq>\d+)'
    )
)
def _policy_emits(world, domain, seq):
    assert world.err is None, f"refused: {world.err}"
    [reaction] = world.answer.reactions
    assert reaction.trigger_sequence == int(seq)
    assert [c.cover.domain for c in reaction.commands] == [domain]


@then("the command carries no router-stamped provenance")
def _no_provenance(world):
    [command] = world.answer.reactions[0].commands
    assert command == fx.reserve_for("inventory")


@then("the policy emits no commands")
def _policy_emits_nothing(world):
    assert world.err is None, f"refused: {world.err}"
    assert list(world.answer.reactions) == []


@then(parsers.re(r"the policy handler saw source sequence (?P<seq>\d+)"))
def _policy_saw(world, seq):
    assert [s.sequence for s in world.seen] == [int(seq)]


# ===========================================================================
# projector_dispatch.feature
# ===========================================================================


@given(parsers.re(r"a counter projection(?P<every> over every domain)?$"))
def _counter_projection(world, every):
    world.router = Router()
    domains = (WILDCARD_DOMAIN,) if every else ()
    world.router.register_projector(fx.counter_projector(world.seen, *domains))


@when(parsers.re(r'(?P<n>\d+) events are delivered in domain "(?P<domain>[^"]*)"'))
def _delivered_in(world, n, domain):
    book = types_pb2.EventBook(cover=types_pb2.Cover(domain=domain))
    book.pages.extend(fx.page_of(counter_pb2.Increased(), s) for s in range(int(n)))
    world.answer_of(world.router.dispatch_projector, book)


@then(parsers.re(r"the projection records (?:(?P<n>\d+) events?|(?P<nothing>nothing))"))
def _projection_records(world, n, nothing):
    assert world.err is None, f"refused: {world.err}"
    projection = counter_pb2.CounterProjectorState.FromString(
        world.answer.projection.value
    )
    assert projection.count == (0 if nothing else int(n))
    assert world.answer.projector == "CounterProjector"


# ===========================================================================
# handling.feature
# ===========================================================================


@given(
    parsers.re(
        r'an "(?P<binary>[^"]*)" business binary built as version "(?P<version>[^"]*)"'
    )
)
def _built_as(world, binary, version):
    world.version = version


@given(
    parsers.re(r'a "Fulfillment" process manager built as version "(?P<version>[^"]*)"')
)
def _fulfillment_built(world, version):
    world.pm_version = version


@when(
    parsers.re(
        r"the process manager handles a StartFulfillment command ?(?P<writing>.*)$"
    )
)
def _pm_handles(world, writing):
    writes = writing.startswith("and writes")
    world.hosting(fx.fulfillment(world.pm_version, writes, world.seen))
    world.answer_of(
        world.router.dispatch, fx.order_command(order_pb2.StartFulfillment())
    )


@then("the answer's snapshot state is the process manager's state after the command")
def _pm_state(world):
    state = order_pb2.FulfillmentState.FromString(
        world.answer.events.snapshot.state.value
    )
    placed = len(world.answer.events.pages)
    assert state.placed == placed
    assert world.answer.events.snapshot.state.type_url == "/orders.v1.FulfillmentState"


@then("none of the answer's event pages carry a state")
def _pages_carry_no_state(world):
    names = {fx.fq_of(p.payload) for p in world.answer.events.pages}
    assert fx.fq(order_pb2.FulfillmentState) not in names


@then(parsers.re(r'the answer reports rules version "(?P<version>[^"]*)"'))
def _reports_version(world, version):
    assert world.err is None, f"refused: {world.err}"
    assert world.answer.rules_version == version


@given(
    parsers.re(
        r'a delivered snapshot of the order state recorded under rules version "(?P<version>[^"]*)"'
    )
)
def _snapshot_under(world, version):
    world.snapshot = types_pb2.Snapshot(
        state=pack(fx.order_state(["pear"])), rules_version=version
    )


@given(
    parsers.re(
        r'a delivered snapshot at sequence (?P<seq>\d+) of the order state with item "(?P<item>[^"]*)"'
    )
)
def _snapshot_at(world, seq, item):
    world.snapshot = types_pb2.Snapshot(
        sequence=int(seq), state=pack(fx.order_state([item]))
    )


@given(parsers.re(r"the delivery's pages are (?P<pages>.+)$"))
def _delivery_pages(world, pages):
    found = re.findall(r'(\w+) "([^"]*)" at sequence (\d+)', pages)
    world.pages = [_order_event(kind, value, int(seq)) for kind, value, seq in found]


@then("business code decides on the snapshot's state")
def _decides_on_snapshot(world):
    assert world.last().items == ["pear"]


@then("the command is accepted")
def _accepted(world):
    assert world.err is None, f"refused: {world.err}"


# --- one component -----------------------------------------------------------


def _component(kind: str, name: str):
    if kind == "aggregate":
        return AggregateDispatch(name, name, Rebuilder(order_pb2.OrderState))
    if kind == "process manager":
        return ProcessManagerDispatch(name, name, Rebuilder(order_pb2.FulfillmentState))
    if kind == "policy":
        return PolicyDispatch(name, "order", "inventory")
    return ProjectorDispatch(name, counter_pb2.CounterProjectorState)


_REGISTER = {
    "aggregate": "register_aggregate",
    "process manager": "register_process_manager",
    "policy": "register_policy",
    "projector": "register_projector",
}


@given(
    parsers.re(
        r'the "order" business binary has its "(?P<name>[^"]*)" aggregate registered'
    )
)
def _order_registered(world, name):
    world.order()


@when(
    parsers.re(
        r'an? (?P<kind>aggregate|policy|process manager|projector) component "(?P<name>[^"]*)" '
        r"is registered in the same business process"
    )
)
def _second_component(world, kind, name):
    register = getattr(world.router, _REGISTER[kind])
    world.answer_of(register, _component(kind, name))


@then(
    parsers.re(
        r"the registration is refused with reason (?P<reason>[A-Z_]+), naming the "
        r'"(?P<name>[^"]*)" aggregate already registered'
    )
)
def _registration_refused(world, reason, name):
    assert world.err is not None, "the second component was registered"
    assert world.err.code == reason
    assert dict(world.info().metadata) == {
        "component": name,
        "component_kind": "AGGREGATE",
    }


@then(
    parsers.re(r'the business process describes only the "(?P<name>[^"]*)" aggregate')
)
def _describes_only(world, name):
    desc = world.router.component_descriptor()
    assert (desc.name, desc.kind) == (name, types_pb2.COMPONENT_KIND_AGGREGATE)
    assert desc.rules_version == world.version
    assert desc.state.type_url == "/orders.v1.OrderState"


# --- inputs ------------------------------------------------------------------

_ORDER_EVENTS = {
    "ItemAdded": lambda v: order_pb2.ItemAdded(item=v),
    "OrderNoteAdded": lambda v: order_pb2.OrderNoteAdded(note=v),
}


def _order_event(kind: str, value: str, sequence: int) -> types_pb2.EventPage:
    return fx.page_of(_ORDER_EVENTS[kind](value), sequence)


def _order_input(text: str) -> types_pb2.ContextualCommand:
    """The input a step names: ``AddItem "apple"``, a command business
    code answers with no events, or ``a fact ItemDelivered "apple"``."""
    if text == "a command that business code answers with no events":
        return fx.order_command(order_pb2.Acknowledge(item="pear"))
    fact = re.fullmatch(r'a fact (\w+) "([^"]*)"', text)
    if fact:
        request = fx.order_command(order_pb2.AddItem())
        return fx.as_fact(request, pack(order_pb2.ItemDelivered(item=fact[2])))
    command, item = re.fullmatch(r'(\w+) "([^"]*)"', text).groups()
    payload = any_pb2.Any(
        type_url=f"/orders.v1.{command}",
        value=order_pb2.AddItem(item=item).SerializeToString(),
    )
    request = fx.order_command(order_pb2.AddItem())
    request.command.page.command.payload.CopyFrom(payload)
    return request


def _ledger_fact(fact: str, prior: int) -> types_pb2.ContextualCommand:
    request = _ledger_command("ledger-1", list(range(prior)))
    fx.as_fact(request, any_pb2.Any(type_url="/test.counter." + fact))
    return request


@when(
    parsers.re(
        r"(?P<input>.+?) is handled"
        r"(?: (?P<mode>STRICT|AGGREGATE_HANDLES|COMMUTATIVE) at expected sequence (?P<expected>\d+))?"
        r" over (?P<over>.+?)"
        r'(?: with nonce "(?P<nonce>[^"]*)")?$'
    )
)
def _handled(world, input, mode, expected, over, nonce):
    ledger = re.fullmatch(r"an? (\w+) fact", input)
    if ledger:
        prior = re.match(r"(\d+|no) prior", over)[1]
        request = _ledger_fact(ledger[1], 0 if prior == "no" else int(prior))
        world.answer_of(world.router.dispatch, request)
        return
    if over in ("that snapshot", "that delivery"):
        events = fx.history(world.pages, world.snapshot)
    else:
        found = re.findall(r'(\w+) "([^"]*)"', over)
        events = fx.history([_order_event(k, v, s) for s, (k, v) in enumerate(found)])
    request = _order_input(input)
    request.events.CopyFrom(events)
    if mode is not None:
        handling = request.command.handling
        handling.conflict_resolution = types_pb2.ConflictResolution.Value(
            "CONFLICT_RESOLUTION_" + mode
        )
        handling.expected_sequence = int(expected)
    if nonce is not None:
        request.conflict_nonce = nonce.encode()
    if world.recorded_at is not None:
        request.recorded_at.CopyFrom(world.recorded_at)
    world.answer_of(world.order().dispatch, request)


@then(
    parsers.re(
        r"the answer carries the order state with (?P<items>.+) as its snapshot state"
    )
)
def _answer_state(world, items):
    assert world.err is None, f"refused: {world.err}"
    names = _quoted(items)
    delivered = names[-1:] if items.endswith(" delivered") else []
    expected = fx.order_state(names[: len(names) - len(delivered)], delivered)
    snapshot = world.answer.events.snapshot
    assert snapshot.state.type_url == "/orders.v1.OrderState"
    assert order_pb2.OrderState.FromString(snapshot.state.value) == expected


@given("business code that never mentions snapshots")
def _never_mentions_snapshots(world):
    pass


# --- conflicts ---------------------------------------------------------------


def _settles(nonce: bytes | None):
    def behave(_cmd, _state, ctx):
        ctx.conflict.resolve_conflicts(ctx.conflict.window, nonce or ctx.conflict.nonce)

    return behave


@given("business code settles conflicts with the nonce it was given")
def _settles_given(world):
    world.behave = _settles(None)


@given(parsers.re(r'business code settles conflicts with nonce "(?P<nonce>[^"]*)"'))
def _settles_with(world, nonce):
    world.behave = _settles(nonce.encode())


@given("business code does not settle conflicts")
def _does_not_settle(world):
    world.behave = None


@then(
    parsers.re(r"business code saw expected sequence (?P<seq>\d+) under (?P<mode>\w+)")
)
def _saw_expected(world, seq, mode):
    ctx = world.last().ctx
    assert ctx.expected_sequence == int(seq)
    assert ctx.conflict_resolution == types_pb2.ConflictResolution.Value(
        "CONFLICT_RESOLUTION_" + mode
    )


@then("business code saw no conflict window and no nonce")
def _saw_no_conflict(world):
    assert world.last().ctx.conflict is None


@then(
    parsers.re(r"business code saw the window at sequences (?P<a>\d+) and (?P<b>\d+)")
)
def _saw_window(world, a, b):
    conflict = world.last().ctx.conflict
    assert [p.sequence for p in conflict.window] == [int(a), int(b)]
    assert conflict.nonce == world.sent.conflict_nonce


@then(
    parsers.re(
        r'the answer reports (?:no resolved nonce|resolved nonce "(?P<nonce>[^"]*)")'
    )
)
def _reports_nonce(world, nonce):
    assert world.err is None, f"refused: {world.err}"
    assert world.answer.HasField("conflict")
    assert world.answer.conflict.resolved_nonce == (nonce or "").encode()


@then("the answer carries business code's events")
def _carries_events(world):
    item = order_pb2.AddItem.FromString(
        world.sent.command.page.command.payload.value
    ).item
    assert list(world.answer.events.pages) == [
        fx.page_of(order_pb2.ItemAdded(item=item))
    ]


@then(
    parsers.re(
        r'the answer reports window units "(?P<window>[^"]*)" and command units "(?P<mine>[^"]*)"'
    )
)
def _reports_units(world, window, mine):
    assert world.err is None, f"refused: {world.err}"
    report = world.answer.conflict
    assert (list(report.window_units), list(report.command_units)) == ([window], [mine])
    assert report.resolved_nonce == b""


@then(parsers.re(r"business code saw items (?P<items>.+) in its state"))
def _saw_items(world, items):
    assert world.last().items == sorted(_quoted(items))


# --- errors ------------------------------------------------------------------


def _failing(error: Exception, logs: str | None = None):
    def behave(_cmd, _state, _ctx):
        if logs is not None:
            fx.log_for_itself(logs)
        raise error

    return behave


_LOGS = r', and logs "(?P<logs>[^"]*)" for itself'


@given(
    parsers.re(
        r"business code rejects AddItem with code (?P<code>\w+) and arg (?P<arg>\w+) "
        r'"(?P<value>[^"]*)"' + _LOGS
    )
)
def _rejects(world, code, arg, value, logs):
    world.behave = _failing(Rejection(code, **{arg: value}), logs)


@given(
    parsers.re(
        r"business code refuses AddItem with the refusal object ItemOutOfStock with item "
        r'"(?P<item>[^"]*)" and on_hand (?P<on_hand>\d+)'
    )
)
def _refuses(world, item, on_hand):
    world.behave = _failing(
        Refused(order_pb2.ItemOutOfStock(item=item, on_hand=int(on_hand)))
    )


@given(
    parsers.re(
        r'business code for AddItem fails with an unclassified error whose text is "(?P<text>[^"]*)"'
    )
)
def _fails_unclassified(world, text):
    world.behave = _failing(UnclassifiedError(text))


@given(parsers.re(r'business code for AddItem panics with "(?P<text>[^"]*)"'))
def _panics(world, text):
    world.behave = _failing(IndexError(text))


@given(
    parsers.re(
        r"business code for AddItem fails with code (?P<code>\w+) and arg (?P<arg>\w+) "
        r'"(?P<value>[^"]*)"' + _LOGS
    )
)
def _fails_bug(world, code, arg, value, logs):
    world.behave = _failing(Bug(code, **{arg: value}), logs)


@given(
    parsers.re(
        r"business code for AddItem fails with a retryable bug under (?P<status>\w+), "
        r'code (?P<code>\w+) and arg (?P<arg>\w+) "(?P<value>[^"]*)"' + _LOGS
    )
)
def _fails_retryable(world, status, code, arg, value, logs):
    world.behave = _failing(RetryableBug(code, GrpcCode[status], **{arg: value}), logs)


@given(
    parsers.re(
        r"business code rejects AddItem for immediate return with code (?P<code>\w+) and arg "
        r'(?P<arg>\w+) "(?P<value>[^"]*)"' + _LOGS
    )
)
def _rejects_immediate(world, code, arg, value, logs):
    world.behave = _failing(Immediate(code, **{arg: value}), logs)


@then(
    parsers.re(
        r"(?:the command|it) is refused (?P<status>\w+) with reason (?P<reason>\w+), "
        r"which is also its status message"
    )
)
def _refused_with(world, status, reason):
    assert world.err is not None, f"answered, not refused: {world.answer}"
    wire = world.err.status()
    assert (wire.code, wire.message) == (GrpcCode[status], reason)
    assert world.info().reason == reason
    assert world.info().domain == "angzarr.io"


@then(parsers.re(r"the refusal's error info carries (?P<metadata>.+)$"))
def _info_carries(world, metadata):
    carried = dict(world.info().metadata)
    if metadata == "no other metadata":
        assert carried == {}
        return
    for part in re.split(r" and ", metadata):
        absent = re.fullmatch(r"no (\S+)", part)
        if absent:
            assert absent[1] not in carried, carried
            continue
        name, _, value = part.partition(" ")
        assert name in carried, carried
        if value:
            assert carried[name] == value.strip('"'), carried


@then(parsers.re(r'no part of the answer carries the text "(?P<text>[^"]*)"'))
def _no_text(world, text):
    assert world.err is not None
    assert text.encode() not in world.err.status().SerializeToString()
    assert text not in str(world.err)


@then(
    parsers.re(
        r"the refusal's status details carry the ItemOutOfStock with item "
        r'"(?P<item>[^"]*)" and on_hand (?P<on_hand>\d+)'
    )
)
def _details_carry(world, item, on_hand):
    refusal = order_pb2.ItemOutOfStock()
    assert world.err.status().details[1].Unpack(refusal)
    assert refusal == order_pb2.ItemOutOfStock(item=item, on_hand=int(on_hand))


@then("business code is not invoked")
def _not_invoked(world):
    assert world.seen == []


# --- C-0670: one reason key per condition ------------------------------------


@when(parsers.re(r"the state fold fails on a persisted event at sequence (?P<seq>\d+)"))
def _fold_fails_persisted(world, seq):
    items = ["pear"] * int(seq) + [fx.ROTTEN]
    events = fx.history([_order_event("ItemAdded", v, s) for s, v in enumerate(items)])
    request = _order_input('AddItem "apple"')
    request.events.CopyFrom(events)
    world.answer_of(world.order().dispatch, request)


@when(
    parsers.re(
        r"the state fold fails on an event the command emitted at sequence (?P<seq>\d+)"
    )
)
def _fold_fails_emitted(world, seq):
    events = fx.history([_order_event("ItemAdded", "pear", s) for s in range(int(seq))])
    request = _order_input(f'AddItem "{fx.ROTTEN}"')
    request.events.CopyFrom(events)
    world.answer_of(world.order().dispatch, request)


@when(parsers.re(r'a command of type "(?P<url>[^"]*)" arrives'))
def _command_of_type(world, url):
    request = _order_input('AddItem "apple"')
    request.command.page.command.payload.type_url = url
    world.answer_of(world.order().dispatch, request)


@when(parsers.re(r'a fact of type "(?P<url>[^"]*)" arrives'))
def _fact_of_type(world, url):
    request = fx.as_fact(_order_input('AddItem "apple"'), any_pb2.Any(type_url=url))
    world.answer_of(world.order().dispatch, request)


def _refused_fact(command_type: str) -> types_pb2.ContextualCommand:
    refused = types_pb2.CommandRefused()
    refused.command.page.command.payload.type_url = command_type
    return fx.as_fact(_order_input('AddItem "apple"'), pack(refused))


@when(
    parsers.re(r"a CommandRefused for (?P<command>\w+) arrives with no on_refused_\w+")
)
def _refused_for(world, command):
    world.answer_of(world.order().dispatch, _refused_fact(f"/orders.v1.{command}"))


@when("a CommandRefused fact arrives whose payload does not decode")
def _refused_undecodable(world):
    request = _refused_fact("/orders.v1.AddItem")
    request.command.page.fact.payload.value = b"\xff\xff\xff"
    world.answer_of(world.order().dispatch, request)


@when(
    parsers.re(
        r'a policy is dispatched to the business process hosting the "order" aggregate'
    )
)
def _policy_to_aggregate(world):
    world.answer_of(world.order().dispatch_policy, types_pb2.EventBook())


@when("the router is built with two components")
def _two_components(world):
    world.order()
    world.answer_of(
        world.router.register_projector, _component("projector", "OrderSummary")
    )


@when("a COMMUTATIVE command reaches a state with no resolver")
def _no_resolver(world):
    world.hosting(fx.order_aggregate(world.version, world.seen, commutative=False))
    request = _order_input('AddItem "apple"')
    request.command.handling.conflict_resolution = (
        types_pb2.CONFLICT_RESOLUTION_COMMUTATIVE
    )
    world.answer_of(world.router.dispatch, request)


# --- policies ----------------------------------------------------------------


@given(
    parsers.re(
        r'an "OrderFulfillment" policy translating "(?P<source>[^"]*)" events into '
        r'"(?P<destination>[^"]*)" commands'
    )
)
def _fulfillment_policy(world, source, destination):
    world.router = Router()
    world.policy = PolicyDispatch("OrderFulfillment", source, destination)
    world.router.register_policy(world.policy)


@given("its business code answers each OrderPlaced event with one ReserveStock command")
def _answers_reserve(world):
    def placed(_event, ctx):
        world.seen.append(fx.Observed(sequence=ctx.sequence))
        book = types_pb2.CommandBook(cover=fx.cover_of("inventory", "inv-7"))
        book.page.command.payload.CopyFrom(pack(order_pb2.ReserveStock(item="apple")))
        return [book]

    world.policy.on_event_with_context(fx.fq(order_pb2.OrderPlaced), placed)


@given(
    parsers.re(
        r"its business code answers an OrderPlaced event with a ShipOrder command for "
        r'"(?P<domain>[^"]*)" that names no root, no edition and another correlation'
    )
)
def _answers_ship(world, domain):
    world.command = types_pb2.CommandBook(cover=types_pb2.Cover(domain=domain))
    world.command.page.command.payload.CopyFrom(pack(order_pb2.ShipOrder()))
    world.command.page.correlation_id.value = fx.root_of("another correlation")
    world.policy.on_event(fx.fq(order_pb2.OrderPlaced), lambda _e, _c: [world.command])


_ORDER_PAGES = {
    "OrderPlaced": order_pb2.OrderPlaced,
    "OrderNoteAdded": order_pb2.OrderNoteAdded,
}


@when(parsers.re(r"a book holding (?P<pages>.+) is handled$"))
def _book_handled(world, pages):
    book = types_pb2.EventBook(cover=fx.cover_of("order", "order-1"))
    for kind, seq in re.findall(r"(\w+) at sequence (\d+)", pages):
        book.pages.append(fx.page_of(_ORDER_PAGES[kind](), int(seq)))
    world.answer_of(world.router.dispatch_policy, book)


@when(parsers.re(r"the (?P<event>\w+) event is handled$"))
def _the_event_handled(world, event):
    book = types_pb2.EventBook(cover=fx.cover_of("order", "order-1"))
    book.pages.append(fx.page_of(_ORDER_PAGES[event](), 3))
    world.answer_of(world.router.dispatch_policy, book)


@then("the answer holds two reactions, in page order:")
def _reactions(world, datatable):
    assert world.err is None, f"refused: {world.err}"
    rows = [dict(zip(datatable[0], row)) for row in datatable[1:]]
    reactions = world.answer.reactions
    assert [r.trigger_sequence for r in reactions] == [
        int(r["trigger sequence"]) for r in rows
    ]
    for reaction, row in zip(reactions, rows):
        assert row["commands"] == "one ReserveStock"
        [command] = reaction.commands
        assert fx.fq_of(command.page.command.payload) == fx.fq(order_pb2.ReserveStock)


@then(parsers.re(r"no reaction names sequence (?P<seq>\d+)"))
def _no_reaction_for(world, seq):
    assert int(seq) not in [r.trigger_sequence for r in world.answer.reactions]


@then("the answer's one reaction carries that ShipOrder command unchanged")
def _ship_unchanged(world):
    [reaction] = world.answer.reactions
    assert list(reaction.commands) == [world.command]


@then("the answer is not refused")
def _not_refused(world):
    assert world.err is None, f"refused: {world.err}"


@given(
    parsers.re(
        r'an "OrderFulfillment" policy whose ReserveStock refusals are translated by a '
        r'callback returning the "order" fact StockUnavailable'
    )
)
def _translating_policy(world):
    world.router = Router()
    world.router.register_policy(fx.order_fulfillment(world.seen, translate=True))


@given('an "OrderFulfillment" policy with no callback for ReserveStock refusals')
def _untranslating_policy(world):
    world.router = Router()
    world.router.register_policy(fx.order_fulfillment(world.seen))


@when("the policy is given the refusal of a ReserveStock it sent")
def _given_refusal(world):
    refusal = policy_pb2.Refusal()
    refusal.event.cover.CopyFrom(fx.cover_of("order", "order-1"))
    refusal.event.pages.append(fx.page_of(order_pb2.OrderPlaced(), 3))
    refusal.command.cover.CopyFrom(fx.cover_of("inventory", "inv-7"))
    refusal.command.page.command.payload.CopyFrom(
        pack(order_pb2.ReserveStock(item="apple"))
    )
    refusal.command.page.command_id.value = fx.root_of("reserve-1")
    refusal.response.diagnostic.code = "OUT_OF_STOCK"
    world.answer_of(world.router.dispatch_refusal, refusal)


@then("the callback is called with that refusal")
def _callback_called(world):
    [seen] = world.seen
    assert seen.refusal == world.sent
    assert seen.ctx.cover == world.sent.command.cover
    assert seen.ctx.command_id == world.sent.command.page.command_id


@then("the answer's fact page carries StockUnavailable as its only content")
def _fact_only(world):
    assert world.err is None, f"refused: {world.err}"
    page = world.answer.fact.page
    expected = types_pb2.CommandPage(
        fact=types_pb2.Fact(payload=pack(order_pb2.StockUnavailable(item="apple")))
    )
    assert page == expected


@then("the answer's fact sets no cover, no handling and no identity")
def _fact_unaddressed(world):
    fact = world.answer.fact
    assert not fact.HasField("cover")
    assert not fact.HasField("handling")
    for identity in ("command_id", "correlation_id", "causation_id", "fact_origin"):
        assert not fact.page.HasField(identity)


@then("the answer carries no fact")
def _no_fact(world):
    assert world.err is None, f"refused: {world.err}"
    assert not world.answer.HasField("fact")


# --- when the input was accepted -------------------------------------------


@given(parsers.re(r'the delivery says its input was accepted at "(?P<at>[^"]*)"'))
def _accepted_at(world, at):
    world.recorded_at = timestamp_pb2.Timestamp()
    world.recorded_at.FromJsonString(at)


@then(parsers.re(r'business code read "(?P<at>[^"]*)" as when its input was accepted'))
def _read_accepted(world, at):
    assert world.last().ctx.recorded_at.ToJsonString() == at


@then("business code read no clock of its own for it")
def _no_clock(world):
    assert world.last().ctx.recorded_at == world.recorded_at
