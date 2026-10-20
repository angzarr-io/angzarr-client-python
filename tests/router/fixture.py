"""The conformance fixtures in Python, implementing the angzarr-generated
Handler protocols (gen/test/counter/*_angzarr.py). Same behavior the Rust
core and Go binding implement; the dispatch wiring is now generated, so these
are the proof the generated Python seam is faithful. Registered via the
generated register_* helpers in each scenario's world.

``observed``, when supplied, records the CommandContext and rebuilt count each
command handler saw — the historical-state evidence the suite asserts, since
state never crosses the boundary."""

from __future__ import annotations

from dataclasses import dataclass

from angzarr_client.proto.io.angzarr.v1 import process_manager_pb2, types_pb2
from angzarr_client.router import CommandContext, pack, reject

from .builders import FQ_RESERVE, type_url
from .gen.test.counter import counter_pb2


@dataclass
class Observation:
    """One record of what a command handler saw: the context and rebuilt
        state count. State is router-owned: every handler receives a decoded
    copy."""

    cctx: CommandContext
    count: int


# --- CounterAggregate ---


class CounterAggregate:
    """Implements counter_aggregate_angzarr.CounterAggregateHandler. The
    generated rebuilder folds Increased via apply_increased; the typed-emit
    handler returns Increased events the wiring packs into the EventBook."""

    def __init__(self, observed: list[Observation] | None = None):
        self.observed = observed

    def increase_by(self, cmd, state, cctx: CommandContext):
        if self.observed is not None:
            self.observed.append(Observation(cctx=cctx, count=state.count))
        if cmd.n == 0:
            raise reject("VALUE_NOT_POSITIVE", "increase amount must be positive")
        return [counter_pb2.Increased() for _ in range(cmd.n)]

    def fail_hard(self, cmd, state, cctx: CommandContext):
        raise RuntimeError("hard failure")

    def apply_increased(self, state, event, ctx) -> None:
        state.count += 1


# --- OrderSaga ---


class OrderSaga:
    """Implements order_saga_angzarr.OrderSagaHandler."""

    def increased(self, event, source):
        return [reserve_command()]


# --- CounterProjector ---


class CounterProjector:
    """Implements counter_projector_angzarr.CounterProjectorHandler. The
    router answers the Projection, carrying the folded projection."""

    def increased(self, projection, event, ctx) -> None:
        projection.count += 1


# --- OrderProcessManager ---


class OrderProcessManager:
    """Implements order_process_manager_angzarr.OrderProcessManagerHandler.
    Reacts with a Reserve command for "inventory" and one unaddressed process
    event recording the count its rebuilt state held."""

    def increased(self, event, state, trigger_cover):
        resp = process_manager_pb2.ProcessManagerHandleResponse()
        resp.commands.append(reserve_command())
        resp.process_events.append(
            process_event(counter_pb2.OrderProcessManagerState(count=state.count))
        )
        return resp

    def apply_increased(self, state, event, ctx) -> None:
        state.count += 1


def reserve_command():
    """One Reserve command for "inventory", payload only."""
    cmd = types_pb2.CommandBook()
    cmd.cover.domain = "inventory"
    cmd.page.command.payload.type_url = type_url(FQ_RESERVE)
    return cmd


def process_event(state):
    """One unaddressed process-event book whose single page carries
    ``state``."""
    book = types_pb2.EventBook()
    book.pages.add().payload.CopyFrom(pack(state))
    return book


def recorded_states(resp, message_type) -> list:
    """The states the reacting PMs of ``message_type`` recorded in ``resp``'s
    process events, one per reacting PM."""
    out = []
    for book in resp.process_events:
        for page in book.pages:
            if (
                page.payload.type_url.rsplit("/", 1)[-1]
                == message_type.DESCRIPTOR.full_name
            ):
                out.append(message_type.FromString(page.payload.value))
    return out
