"""Unified Router builder.

Registration-only surface in R3. Mode inference + kind validation arrive in
R4 / R5; dispatch logic arrives in R6+. See TIER5_PLAN.md for the full round
sequence.
"""

from __future__ import annotations

from typing import Any


class BuildError(Exception):
    """Raised when the builder cannot produce a valid runtime router."""


class Router:
    """Fluent builder that collects handler instances for the unified router.

    Usage::

        Router("agg-service")
            .with_handler(Player(db_pool))
            .with_handler(Hand(rng))
            .build()

    ``.build()`` returns a typed runtime router (``CommandHandlerRouter``,
    ``SagaRouter``, ``ProcessManagerRouter``, or ``ProjectorRouter``). Mixed
    handler kinds are rejected at build time (R4).
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self._handlers: list[Any] = []

    def with_handler(self, handler: Any) -> "Router":
        """Register a handler instance.

        The instance's class must carry one of the four kind decorators
        (@command_handler, @saga, @process_manager, @projector). Registering
        an undecorated instance raises :class:`BuildError`.
        """
        cls = type(handler)
        kind = getattr(cls, "__angzarr_kind__", None)
        if kind is None:
            raise BuildError(
                f"{cls.__name__} has no @command_handler / @saga / "
                f"@process_manager / @projector decorator — cannot register"
            )
        self._handlers.append(handler)
        return self

    def build(self) -> Any:
        """Produce a typed runtime router.

        Empty → :class:`BuildError`. Mixed kinds → :class:`BuildError`.
        Homogeneous → ``CommandHandlerRouter`` / ``SagaRouter`` /
        ``ProcessManagerRouter`` / ``ProjectorRouter`` per the shared kind.
        """
        if not self._handlers:
            raise BuildError(f"no handlers registered on Router {self.name!r}")

        kinds = {type(h).__angzarr_kind__ for h in self._handlers}
        if len(kinds) > 1:
            raise BuildError(
                f"cannot mix handler kinds on one Router {self.name!r}: "
                f"{sorted(kinds)}"
            )

        # Deferred imports avoid circular references through __init__.
        from .runtime import (
            CommandHandlerRouter,
            ProcessManagerRouter,
            ProjectorRouter,
            SagaRouter,
        )
        from .validation import validate_handler

        for h in self._handlers:
            validate_handler(h)

        (kind,) = kinds
        if kind == "command_handler":
            return CommandHandlerRouter(self.name, self._handlers)
        if kind == "saga":
            return SagaRouter(self.name, self._handlers)
        if kind == "process_manager":
            return ProcessManagerRouter(self.name, self._handlers)
        if kind == "projector":
            return ProjectorRouter(self.name, self._handlers)
        raise BuildError(f"unknown handler kind {kind!r}")
