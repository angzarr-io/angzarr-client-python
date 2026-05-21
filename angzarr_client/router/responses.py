"""Response types returned by saga / process-manager / rejection handlers.

Simple containers used by user code as the return value of ``@handles`` methods
on sagas (``SagaHandlerResponse``), process managers (``ProcessManagerResponse``),
and ``@rejected`` handlers (``RejectionHandlerResponse``). Kept separate from the
dispatch / runtime modules so application code can import them without pulling
in the dispatch machinery.

Mirrors the Rust ``router::responses`` module so cross-language users can
reach the same types at the same logical path.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from angzarr_client.proto.angzarr.v1 import types_pb2 as types


class SagaHandlerResponse:
    """Return value of a saga ``@handles`` method.

    Carries commands to forward to target domains and/or facts (events) to
    inject into other aggregates. Both are optional.
    """

    def __init__(
        self,
        commands: list[types.CommandBook] | None = None,
        events: list[types.EventBook] | None = None,
    ) -> None:
        self.commands = commands or []
        self.events = events or []


class ProcessManagerResponse:
    """Return value of a process-manager ``@handles`` method.

    - ``commands``: commands forwarded to target domains
    - ``process_events``: list of EventBooks recorded into the PM's own
      event stream (state). Multiple books concatenate into the wire's
      single ``ProcessManagerHandleResponse.process_events`` (first
      non-empty book's cover wins).
    - ``facts``: facts injected into other aggregates without a command

    Audit finding #56 (Option B — list[EventBook]): aligns with Rust's
    ``Vec<EventBook>`` and matches the existing ``commands`` / ``facts``
    list shapes for symmetry.
    """

    def __init__(
        self,
        commands: list[types.CommandBook] | None = None,
        process_events: list[types.EventBook] | None = None,
        facts: list[types.EventBook] | None = None,
    ) -> None:
        self.commands = commands or []
        self.process_events = process_events or []
        self.facts = facts or []


@dataclass
class RejectionHandlerResponse:
    """Return shape of a saga ``@rejected`` handler method.

    Carries compensation events to persist locally and an optional
    ``Notification`` to forward upstream. Mirrors Rust's
    ``router::responses::RejectionHandlerResponse`` field-for-field.

    Audit finding #56 (Option B — list[EventBook]): aligns with Rust's
    ``Vec<EventBook>``. Multiple books concatenate downstream; first
    non-empty book's cover wins.
    """

    events: list[types.EventBook] = field(default_factory=list)
    """Events to persist to own state (compensation)."""

    notification: types.Notification | None = None
    """Notification to forward upstream (rejection propagation)."""
