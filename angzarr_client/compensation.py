"""A process manager's compensation (angzarr decision 14.74): each step it
completes paired, in code, with the undo that reverses it, and the rollback
built from them, so an author never writes it twice. angzarr-embedded's
markup (``markup::Compensation``) is its Rust twin; the handler-contract
tier's ``compensation.feature`` keeps the two equal.

The journal is the PM's own events, folded into its state: a step is
completed once the event recording its reply is folded, and the state says
so (a step's ``recorded``). An undo is an event of the PM's own, built from
that record, which the PM's outbound policy turns into what the step's
destination declares: a fact where it can (decision 14.69), so an undo is
never refused and compensation never loops::

    compensation = Compensation(
        failed=lambda state, reason: Failed(reason=reason),
        rolled_back=lambda state: state.failed,
        compensation_failed=lambda state, step: CompensationFailed(step=step),
    ).step(
        "first step",
        completed_by=FirstDone,
        undoes=their_pb2.UndoFirst,
        recorded=lambda state: FirstDone(state.first_id) if state.first_id else None,
        undo=lambda done: FirstUndoDue(done.first_id),
    )
    events = compensation.rollback(state, "the second step was refused")

Which event undoes which step is known only here, so registering a step
checks it against the descriptor (the owner's ruling Q10): an undo whose
target its message declares a command (``(command)`` without ``fact``),
which may be refused, is warned of (:attr:`Compensation.warnings`, and
logged); a refused one's refusal, reaching the PM, records
CompensationFailed (:meth:`Compensation.undo_refused`).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar

from .diagnostic import display
from .error_codes import codes
from .proto.io.angzarr.v1 import options_pb2

_log = logging.getLogger(__name__)


def _full_name(message: Any) -> str:
    """The full name of a protobuf message class or descriptor."""
    return getattr(message, "DESCRIPTOR", message).full_name


def _is_command(message: Any) -> bool:
    """Whether ``message`` (a protobuf message class or descriptor) is
    declared a command: a ``(command)`` entry without ``fact``."""
    options = getattr(message, "DESCRIPTOR", message).GetOptions()
    if not options.HasExtension(options_pb2.command):
        return False
    return not options.Extensions[options_pb2.command].fact


@dataclass(frozen=True)
class UndoTargetsCommand:
    """A step whose undo becomes a command, which its destination may
    refuse: warned of when the step is registered (a warning, not a
    refusal; some undos really can be refused)."""

    step: str
    command: str
    code: ClassVar[str] = codes.UNDO_TARGETS_COMMAND

    def args(self) -> dict[str, str]:
        """Its args: the step's name and the command's full name."""
        return {"step": self.step, "command": self.command}

    def __str__(self) -> str:
        return display(self.code, self.args())


@dataclass(frozen=True)
class _Step:
    name: str
    completed_by: type
    target: str
    recorded: Callable[[Any], Any]
    undo: Callable[[Any], Any]


class Compensation:
    """A process manager's steps, each with its undo, and the events that
    roll it back.

    ``failed(state, reason)`` builds a rollback's failed event, whose fold
    marks the state rolled back, which ``rolled_back(state)`` reads;
    ``compensation_failed(state, step)`` builds the event recording that a
    step's undo was refused, from the state and the step's name."""

    def __init__(
        self,
        *,
        failed: Callable[[Any, Any], Any],
        rolled_back: Callable[[Any], bool],
        compensation_failed: Callable[[Any, str], Any],
    ) -> None:
        self._failed = failed
        self._rolled_back = rolled_back
        self._compensation_failed = compensation_failed
        self._steps: list[_Step] = []
        self._warnings: list[UndoTargetsCommand] = []

    def step(
        self,
        name: str,
        *,
        completed_by: type,
        undoes: Any,
        recorded: Callable[[Any], Any],
        undo: Callable[[Any], Any],
    ) -> Compensation:
        """Register the step ``name`` after those registered (a rollback
        undoes it before them): ``recorded(state)`` reads from the state the
        event (a ``completed_by``) that recorded it done, or None while it
        is not; ``undo(event)`` builds its undo from that event. Its undo
        becomes ``undoes`` (a protobuf message class) at its destination:
        checked here, at registration, against the descriptor; one declared
        a command, which may be refused, is warned of."""
        target = _full_name(undoes)
        self._steps.append(_Step(name, completed_by, target, recorded, undo))
        return self

    @property
    def warnings(self) -> tuple[UndoTargetsCommand, ...]:
        """What registering the steps warned of: each undo that becomes a
        command, which may be refused."""
        return tuple(self._warnings)

    def rollback(self, state: Any, reason: Any) -> list:
        """The rollback of ``state`` for ``reason``: the failed event, then
        the undo of each step the state records done, newest first (the
        reverse of their registration). One handling's events."""
        raise NotImplementedError("rollback")

    def reply(self, state: Any, reply: Any) -> list:
        """The events recording ``reply``, the event that records a step
        done: the reply alone, or, once ``state`` has rolled back (a reply
        that arrived late), the reply and at once its step's undo."""
        raise NotImplementedError("reply")

    def undo_refused(self, state: Any, undoes: Any) -> Any:
        """The event recording that the undo becoming ``undoes`` (a protobuf
        message class, or its full name) was refused: its step's
        CompensationFailed; None when no step's undo becomes it."""
        raise NotImplementedError("undo_refused")
