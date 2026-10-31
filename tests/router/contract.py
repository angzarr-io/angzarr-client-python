"""The handler-contract tier's business code (angzarr-project
features/handler-contract/fixture/FIXTURE.md), written against the router's
registration API by hand, and the deliveries a coordinator would build.

The CounterAggregate, ledger, OrderPolicy and CounterProjector are over
``counter.proto``; the order aggregate, the Fulfillment process manager and
the OrderFulfillment policy over ``order.proto``. Each records what its
business code saw in an :class:`Observed`, so the steps can assert the
evidence the router hands it. None of this is router code.
"""

from __future__ import annotations

import logging
import pathlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from google.protobuf import any_pb2, text_format

from angzarr_client.proto.io.angzarr.v1 import types_pb2
from angzarr_client.router import (
    AggregateDispatch,
    CommandContext,
    FactRecord,
    PageContext,
    PolicyDispatch,
    ProcessManagerDispatch,
    ProjectorDispatch,
    Rebuilder,
    Rejection,
    UnclassifiedError,
    pack,
)

from .gen import counter_pb2, order_pb2

FIXTURE = (
    pathlib.Path(__file__).resolve().parents[2]
    / "angzarr-project"
    / "features"
    / "handler-contract"
    / "fixture"
)

# The item an order applier cannot fold: a fold that fails on it is a
# handler bug (ROUTER_REASON_FOLD_FAILED, ROUTER_REASON_FOLD_FAILED_EMITTED).
ROTTEN = "rotten"

_log = logging.getLogger("tests.router.contract")


def fq(message) -> str:
    return message.DESCRIPTOR.full_name


def fq_of(payload: any_pb2.Any) -> str:
    """The fully-qualified name ``payload``'s type URL names."""
    return payload.type_url.rsplit("/", 1)[-1]


def root_of(label: str) -> bytes:
    """A root for ``label``: UUID v5 (NAMESPACE_OID) of it."""
    return uuid.uuid5(uuid.NAMESPACE_OID, label).bytes


def cover_of(domain: str, label: str | None = None) -> types_pb2.Cover:
    cover = types_pb2.Cover(domain=domain)
    if label is not None:
        cover.root.value = root_of(label)
    return cover


def txtpb(name: str, message):
    """A fixture envelope from ``fixture/txtpb``, parsed whole."""
    return text_format.Parse((FIXTURE / "txtpb" / name).read_text(), message)


def page_of(message, sequence: int = 0) -> types_pb2.EventPage:
    return types_pb2.EventPage(payload=pack(message), sequence=sequence)


@dataclass
class Observed:
    """What one handler call saw."""

    ctx: CommandContext | None = None
    count: int | None = None
    items: list[str] = field(default_factory=list)
    sequence: int | None = None
    cover: types_pb2.Cover | None = None
    refusal: object | None = None


# --- counter.proto ----------------------------------------------------------


def counter_aggregate(seen: list[Observed]) -> AggregateDispatch:
    """The CounterAggregate: Increased folds count += 1; IncreaseBy(n) emits
    n Increased, n == 0 rejected VALUE_NOT_POSITIVE; FailHard fails with the
    unclassified error."""

    def increased(state, event, _ctx):
        counter_pb2.Increased.FromString(event.value)  # a corrupt event fails
        state.count += 1

    def increase_by(cmd, state, ctx):
        seen.append(Observed(ctx=ctx, count=state.count))
        n = counter_pb2.IncreaseBy.FromString(cmd.value).n
        if n == 0:
            raise Rejection("VALUE_NOT_POSITIVE")
        return types_pb2.EventBook(
            pages=[page_of(counter_pb2.Increased()) for _ in range(n)]
        )

    def fail_hard(_cmd, _state, _ctx):
        raise UnclassifiedError("hard failure")

    rebuilder = Rebuilder(counter_pb2.CounterState).apply_with_context(
        fq(counter_pb2.Increased), increased
    )
    return (
        AggregateDispatch("CounterAggregate", "counter", rebuilder)
        .on_command(fq(counter_pb2.IncreaseBy), increase_by)
        .on_command(fq(counter_pb2.FailHard), fail_hard)
    )


def ledger_aggregate(seen: list[Observed]) -> AggregateDispatch:
    """The ledger (context.feature) over CounterState: Increased folds and
    records its sequence; IncreaseBy records its cover and emits one
    Increased; the fact Increased is recorded, flagged by the count it
    brings the ledger to."""

    def increased(state, _event, ctx: PageContext):
        state.count += 1
        seen.append(Observed(sequence=ctx.sequence))

    def increase_by(_cmd, _state, ctx):
        seen.append(Observed(cover=ctx.cover))
        return types_pb2.EventBook(pages=[page_of(counter_pb2.Increased())])

    def record(fact, state, _ctx):
        return FactRecord(fact, [counter_pb2.CounterState(count=state.count + 1)])

    rebuilder = Rebuilder(counter_pb2.CounterState).apply_with_context(
        fq(counter_pb2.Increased), increased
    )
    return (
        AggregateDispatch("Ledger", "ledger", rebuilder)
        .on_command(fq(counter_pb2.IncreaseBy), increase_by)
        .on_fact(fq(counter_pb2.Increased), record)
    )


def reserve_for(domain: str) -> types_pb2.CommandBook:
    """One Reserve command for ``domain``, payload only."""
    book = types_pb2.CommandBook(cover=types_pb2.Cover(domain=domain))
    book.page.command.payload.CopyFrom(pack(counter_pb2.Reserve()))
    return book


def order_policy(destination: str, seen: list[Observed]) -> PolicyDispatch:
    """The OrderPolicy: each Increased becomes one Reserve for
    ``destination``."""

    def increased(_event, ctx: PageContext):
        seen.append(Observed(sequence=ctx.sequence))
        return [reserve_for(destination)]

    return PolicyDispatch("OrderPolicy", "order", destination).on_event_with_context(
        fq(counter_pb2.Increased), increased
    )


def counter_projector(seen: list[Observed], *domains: str) -> ProjectorDispatch:
    """The CounterProjector: each Increased folds count += 1 and records its
    root and sequence."""

    def increased(projection, _event, ctx: PageContext):
        projection.count += 1
        root = ctx.cover.root.value if ctx.cover is not None else b""
        seen.append(Observed(cover=root, sequence=ctx.sequence))

    return (
        ProjectorDispatch("CounterProjector", counter_pb2.CounterProjectorState)
        .for_domains(*(domains or ("counter",)))
        .on_event_with_context(fq(counter_pb2.Increased), increased)
    )


# --- order.proto ------------------------------------------------------------

# What the order's AddItem handler does besides recording ItemAdded(item):
# ``behave(cmd, state, ctx)`` raises the scenario's failure, settles its
# conflict, or does nothing.
Behaviour = Callable[[order_pb2.AddItem, object, CommandContext], None]


def order_units(before: order_pb2.OrderState, after: order_pb2.OrderState) -> list[str]:
    """The order's ``changed()``: one unit per item, the shipping address,
    and the wildcard ``*notes`` for the unmarked notes."""
    keys = sorted(set(before.items) | set(after.items))
    units = [f"items[{k}]" for k in keys if before.items.get(k) != after.items.get(k)]
    if before.shipping_address != after.shipping_address:
        units.append("shipping_address")
    if before.notes != after.notes:
        units.append("*notes")
    return units


def order_aggregate(
    version: str,
    seen: list[Observed],
    behave: Behaviour | None = None,
    commutative: bool = True,
) -> AggregateDispatch:
    """The order aggregate (handling.feature): ItemAdded counts an item,
    OrderNoteAdded sets the notes, ItemDelivered is listed; AddItem records
    ItemAdded, Acknowledge records nothing, the fact ItemDelivered is
    recorded as received."""

    def item_added(state, event, _ctx):
        item = order_pb2.ItemAdded.FromString(event.value).item
        if item == ROTTEN:
            raise UnclassifiedError("cannot fold a rotten item")
        state.items[item] += 1

    def note_added(state, event, _ctx):
        state.notes = order_pb2.OrderNoteAdded.FromString(event.value).note

    def delivered(state, event, _ctx):
        state.delivered.append(order_pb2.ItemDelivered.FromString(event.value).item)

    def add_item(cmd_any, state, ctx):
        cmd = order_pb2.AddItem.FromString(cmd_any.value)
        seen.append(Observed(ctx=ctx, items=sorted(state.items)))
        if behave is not None:
            behave(cmd, state, ctx)
        return types_pb2.EventBook(pages=[page_of(order_pb2.ItemAdded(item=cmd.item))])

    def acknowledge(_cmd, state, ctx):
        seen.append(Observed(ctx=ctx, items=sorted(state.items)))

    def item_delivered(fact, state, ctx):
        seen.append(Observed(ctx=ctx, items=sorted(state.items)))
        return FactRecord.as_received(fact)

    rebuilder = (
        Rebuilder(order_pb2.OrderState)
        .apply_with_context(fq(order_pb2.ItemAdded), item_added)
        .apply_with_context(fq(order_pb2.OrderNoteAdded), note_added)
        .apply_with_context(fq(order_pb2.ItemDelivered), delivered)
    )
    dispatch = (
        AggregateDispatch("order", "order", rebuilder)
        .on_command(fq(order_pb2.AddItem), add_item)
        .on_command(fq(order_pb2.Acknowledge), acknowledge)
        .on_fact(fq(order_pb2.ItemDelivered), item_delivered)
        .with_rules_version(version)
    )
    return dispatch.with_commutative(order_units) if commutative else dispatch


def fulfillment(
    version: str, writes: bool, seen: list[Observed]
) -> ProcessManagerDispatch:
    """The Fulfillment process manager: StartFulfillment records
    FulfillmentStarted (or nothing), which counts as placed."""

    def started(state, _event, _ctx):
        state.placed += 1

    def start(_cmd, _state, ctx):
        seen.append(Observed(ctx=ctx))
        pages = [page_of(order_pb2.FulfillmentStarted())] if writes else []
        return types_pb2.EventBook(pages=pages)

    rebuilder = Rebuilder(order_pb2.FulfillmentState).apply_with_context(
        fq(order_pb2.FulfillmentStarted), started
    )
    return (
        ProcessManagerDispatch("Fulfillment", "fulfillment", rebuilder)
        .on_command(fq(order_pb2.StartFulfillment), start)
        .with_rules_version(version)
    )


def order_command(
    message, events: types_pb2.EventBook | None = None
) -> types_pb2.ContextualCommand:
    """A ContextualCommand carrying ``message`` in the command arm."""
    request = types_pb2.ContextualCommand()
    request.command.cover.CopyFrom(cover_of("order", "order-1"))
    request.command.page.command.payload.CopyFrom(pack(message))
    if events is not None:
        request.events.CopyFrom(events)
    return request


def as_fact(
    request: types_pb2.ContextualCommand, payload: any_pb2.Any
) -> types_pb2.ContextualCommand:
    """``request`` with ``payload`` moved to the Fact arm."""
    request.command.page.fact.payload.CopyFrom(payload)
    return request


def history(
    pages: list[types_pb2.EventPage], snapshot: types_pb2.Snapshot | None = None
) -> types_pb2.EventBook:
    """An order's EventBook of ``pages`` (each numbered) over the starting
    state ``snapshot``, with the next sequence after them."""
    book = types_pb2.EventBook(cover=cover_of("order", "order-1"), pages=pages)
    if snapshot is not None:
        book.snapshot.CopyFrom(snapshot)
    if pages:
        book.next_sequence = pages[-1].sequence + 1
    elif snapshot is not None:
        book.next_sequence = snapshot.sequence + 1
    return book


def order_state(items: list[str], delivered: list[str] = ()) -> order_pb2.OrderState:
    state = order_pb2.OrderState(delivered=list(delivered))
    for item in items:
        state.items[item] += 1
    return state


def order_fulfillment(
    seen: list[Observed], translate: bool | None = None
) -> PolicyDispatch:
    """The OrderFulfillment policy (order -> inventory): each OrderPlaced
    becomes one ReserveStock; with ``translate``, a refused ReserveStock is
    translated into the order fact StockUnavailable."""

    def placed(_event, ctx: PageContext):
        seen.append(Observed(sequence=ctx.sequence))
        book = types_pb2.CommandBook(cover=cover_of("inventory", "inv-7"))
        book.page.command.payload.CopyFrom(pack(order_pb2.ReserveStock(item="apple")))
        return [book]

    def unavailable(refusal, ctx):
        seen.append(Observed(ctx=ctx, refusal=refusal))
        return order_pb2.StockUnavailable(item="apple")

    policy = PolicyDispatch(
        "OrderFulfillment", "order", "inventory"
    ).on_event_with_context(fq(order_pb2.OrderPlaced), placed)
    if translate:
        policy.on_refused(fq(order_pb2.ReserveStock), unavailable)
    return policy


# The build version the test hosts declare (ComponentHost(rules_version=...)).
TEST_RULES_VERSION = "0.0.0+test"


def descriptor_of(
    dispatch, rules_version: str = TEST_RULES_VERSION
) -> types_pb2.ComponentDescriptor:
    """A stand-in for the ComponentDescriptor angzarr-cli generates for
    ``dispatch``'s component (decision 14.42): its name, kind and the version
    its answers report, the table's own, else ``rules_version`` (the host's)."""
    return types_pb2.ComponentDescriptor(
        name=dispatch.name,
        kind=dispatch.kind,
        rules_version=dispatch.rules_version or rules_version,
    )


def described(dispatch, rules_version: str = TEST_RULES_VERSION) -> tuple:
    """``dispatch`` and its descriptor, as a host of ``rules_version``
    registers a component."""
    return dispatch, descriptor_of(dispatch, rules_version)


def log_for_itself(text: str) -> None:
    """Business code's own log line, never the answer's."""
    _log.info(text)
