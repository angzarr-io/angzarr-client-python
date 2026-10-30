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

from ..proto.io.angzarr.v1 import types_pb2
from .context import (
    Applier,
    CommandContext,
    FactRecord,
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
        raise NotImplementedError

    def _decide(self, payload: any_pb2.Any):
        raise NotImplementedError

    def _ingest(self, payload: any_pb2.Any):
        raise NotImplementedError


class ProcessManagerDispatch(AggregateDispatch):
    """A process manager's dispatch table: a domain fed commands by
    policies (decision 14.31), dispatched as an aggregate is."""

    kind = types_pb2.COMPONENT_KIND_PROCESS_MANAGER
