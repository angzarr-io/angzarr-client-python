"""An aggregate's (or a process manager's) dispatch: ``CommandHandlerService.
Handle`` over a ContextualCommand the coordinator built well formed.

The router routes the input by type URL, folds the starting state and every
page it is given, runs decide (a command) or ingest (a fact, or a refusal of
a command this component sent), folds what that recorded, and answers the
events, the state after them and the build version, with a stale command's
conflict report for the coordinator to judge (decision 14.42). It never
upcasts (14.39), loads a snapshot (14.40), admits, guards an envelope or
judges a conflict.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from google.protobuf import any_pb2

from ..proto.io.angzarr.v1 import command_handler_pb2, types_pb2
from .context import (
    Applier,
    CommandContext,
    Conflict,
    FactRecord,
    PageContext,
    book_context,
    call,
    decode,
    fold,
    start_state,
    type_name,
)
from .errors import (
    CONFLICT_RESOLUTION_UNSUPPORTED,
    FOLD_FAILED,
    FOLD_FAILED_EMITTED,
    NO_FACT_HANDLER,
    NO_REFUSAL_HANDLER,
    UNKNOWN_COMMAND_TYPE,
    CodedError,
    unknown_type,
)

# Business code's shapes. ``state`` is a fresh instance of the state message.
#   command:  (cmd Any, state, CommandContext) -> EventBook | None
#   fact:     (fact Any, state, CommandContext) -> FactRecord
#   refused:  (CommandRefused, state, CommandContext) -> FactRecord
#   changed:  (before, after) -> the conflict units whose values differ
CommandThunk = Callable[[any_pb2.Any, object, CommandContext], object]
FactThunk = Callable[[any_pb2.Any, object, CommandContext], FactRecord]
RefusedThunk = Callable[[types_pb2.CommandRefused, object, CommandContext], FactRecord]
ChangedThunk = Callable[[object, object], list]

_COMMUTATIVE = types_pb2.CONFLICT_RESOLUTION_COMMUTATIVE
_AGGREGATE_HANDLES = types_pb2.CONFLICT_RESOLUTION_AGGREGATE_HANDLES
_REFUSED = types_pb2.CommandRefused.DESCRIPTOR.full_name


@dataclass
class Rebuilder:
    """How events fold into a component's state: the state message's
    factory and one applier per fully-qualified event type."""

    factory: Callable[[], object]
    appliers: dict[str, Applier] = field(default_factory=dict)

    def apply(
        self, full_name: str, thunk: Callable[[object, any_pb2.Any], None]
    ) -> Rebuilder:
        """Register an applier ``(state, event)`` for one event type."""
        self.appliers[full_name] = lambda state, event, _ctx: thunk(state, event)
        return self

    def apply_with_context(self, full_name: str, thunk: Applier) -> Rebuilder:
        """Register an applier ``(state, event, PageContext)`` for one event
        type: it also reads the event's book cover, sequence and meta."""
        self.appliers[full_name] = thunk
        return self


def _book(events) -> types_pb2.EventBook:
    return types_pb2.EventBook() if events is None else events


def _copy(state):
    copied = type(state)()
    copied.CopyFrom(state)
    return copied


@dataclass
class AggregateDispatch:
    """One aggregate's dispatch table: its name, domain and rebuilder, its
    command, fact and refusal handlers by fully-qualified type, the build
    version it reports, and the ``changed`` resolver COMMUTATIVE needs."""

    name: str
    domain: str
    rebuilder: Rebuilder
    commands: dict[str, CommandThunk] = field(default_factory=dict)
    facts: dict[str, FactThunk] = field(default_factory=dict)
    refusals: dict[str, RefusedThunk] = field(default_factory=dict)
    rules_version: str = ""
    changed: ChangedThunk | None = None

    kind = types_pb2.COMPONENT_KIND_AGGREGATE

    def on_command(self, full_name: str, thunk: CommandThunk) -> AggregateDispatch:
        """Register the handler of one command type."""
        self.commands[full_name] = thunk
        return self

    def on_fact(self, full_name: str, thunk: FactThunk) -> AggregateDispatch:
        """Register the handler of one fact type: it returns the
        :class:`FactRecord` to record and is never refused."""
        self.facts[full_name] = thunk
        return self

    def on_refused(self, command: str, thunk: RefusedThunk) -> AggregateDispatch:
        """Register ``on_refused_<Command>``: the handler of the
        pass-through ``CommandRefused`` of a ``command`` this component
        sent, returning the :class:`FactRecord` to record."""
        self.refusals[command] = thunk
        return self

    def with_commutative(self, changed: ChangedThunk) -> AggregateDispatch:
        """Allow COMMUTATIVE: ``changed(before, after)`` names the conflict
        units whose values differ between two states."""
        self.changed = changed
        return self

    def with_rules_version(self, version: str) -> AggregateDispatch:
        """Set the build version reported on every answer."""
        self.rules_version = version
        return self

    def handle(self, request: types_pb2.ContextualCommand):
        """``CommandHandlerService.Handle``: one ContextualCommand in, its
        CommandHandlerResponse out, or the CodedError that refuses it."""
        page = request.command.page
        arm = page.WhichOneof("body") or "command"
        payload = getattr(page, arm).payload
        events = request.events
        mode = request.command.handling.conflict_resolution
        if arm == "fact":
            decide = self._ingest(payload)
        else:
            decide = self._decide(payload)
            if mode == _COMMUTATIVE and self.changed is None:
                raise CodedError(CONFLICT_RESOLUTION_UNSUPPORTED)
        ctx = book_context(request.command, events.next_sequence)
        ctx.had_prior_events = bool(events.pages) or events.HasField("snapshot")
        ctx.recorded_at = (
            request.recorded_at if request.HasField("recorded_at") else None
        )

        appliers = self.rebuilder.appliers
        cover = events.cover if events.HasField("cover") else None
        state = start_state(self.rebuilder.factory, events)
        pages = list(events.pages)
        seen = sum(1 for p in pages if p.sequence < ctx.expected_sequence)
        fold(state, pages[:seen], cover, appliers, FOLD_FAILED)
        before = _copy(state) if mode == _COMMUTATIVE else None
        fold(state, pages[seen:], cover, appliers, FOLD_FAILED)
        if mode == _AGGREGATE_HANDLES:
            ctx.conflict = Conflict(pages[seen:], request.conflict_nonce)

        out = call(PageContext(ctx.cover), decide, state, ctx)
        head = _copy(state) if before is not None else None
        emitted = [types_pb2.EventPage() for _ in out.pages]
        for offset, (copy, page) in enumerate(zip(emitted, out.pages)):
            copy.CopyFrom(page)
            copy.sequence = events.next_sequence + offset
        fold(state, emitted, cover, appliers, FOLD_FAILED_EMITTED)

        resp = command_handler_pb2.CommandHandlerResponse(
            events=out, rules_version=self.rules_version
        )
        resp.events.snapshot.state.Pack(state, type_url_prefix="/")
        if ctx.conflict is not None:
            resp.conflict.resolved_nonce = ctx.conflict.resolved_nonce
        if head is not None:
            window = call(PageContext(), self.changed, before, head)
            mine = call(PageContext(), self.changed, head, state)
            resp.conflict.window_units.extend(window)
            resp.conflict.command_units.extend(mine)
        return resp

    def _decide(self, payload: any_pb2.Any):
        thunk = self.commands.get(type_name(payload.type_url))
        if thunk is None:
            raise unknown_type(UNKNOWN_COMMAND_TYPE, payload.type_url)
        return lambda state, ctx: _book(thunk(payload, state, ctx))

    def _ingest(self, payload: any_pb2.Any):
        fq = type_name(payload.type_url)
        thunk = self.facts.get(fq)
        if thunk is not None:
            return lambda state, ctx: thunk(payload, state, ctx).book()
        if fq != _REFUSED:
            raise unknown_type(NO_FACT_HANDLER, payload.type_url)
        refused = decode(types_pb2.CommandRefused, payload.value)
        command_type = refused.command.page.command.payload.type_url
        on_refused = self.refusals.get(type_name(command_type))
        if on_refused is None:
            raise CodedError(NO_REFUSAL_HANDLER, extras={"command_type": command_type})
        return lambda state, ctx: on_refused(refused, state, ctx).book()


class ProcessManagerDispatch(AggregateDispatch):
    """A process manager's dispatch table: a domain fed commands by
    policies (decision 14.31), dispatched as an aggregate is."""

    kind = types_pb2.COMPONENT_KIND_PROCESS_MANAGER
