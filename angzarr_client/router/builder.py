"""Unified Router builder.

Registration-only surface in R3. Mode inference + kind validation arrive in
R4 / R5; dispatch logic arrives in R6+. See TIER5_PLAN.md for the full round
sequence.
"""

from __future__ import annotations

from typing import Any, Callable


class BuildError(Exception):
    """Raised when the builder cannot produce a valid runtime router."""


class Router:
    """Fluent builder that collects handler factories for the unified router.

    Usage::

        Router("agg-service")
            .with_handler(Player, lambda: Player(db_pool))
            .with_handler(Hand, lambda: Hand(rng))
            .build()

    ``.build()`` returns a typed runtime router (``CommandHandlerRouter``,
    ``SagaRouter``, ``ProcessManagerRouter``, or ``ProjectorRouter``). Mixed
    handler kinds are rejected at build time (R4).

    Handlers are registered as ``(cls, factory)`` pairs so the router can
    invoke ``factory()`` per dispatch call to obtain a fresh (or pooled)
    instance. This keeps handler state isolated per request and makes the
    router safe to share across threads.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self._factories: list[tuple[type, Callable[[], Any]]] = []

    def with_handler(
        self, cls: type, factory: Callable[[], Any]
    ) -> "Router":
        """Register a handler class together with a zero-arg factory.

        ``cls`` must carry one of the five kind decorators
        (``@command_handler``, ``@saga``, ``@process_manager``,
        ``@projector``, ``@upcaster``). Registering an undecorated class
        raises :class:`BuildError`.

        ``factory`` is any ``Callable[[], Any]``: a lambda, a bound method on
        a DI container, a ``functools.partial``, a pool-checkout closure, or
        a callable returning a shared singleton. The router never inspects
        the factory body; it only calls it and uses whatever it returns.

        Factories are invoked inside ``dispatch()``, so their latency is on
        the request path. For handlers whose construction is expensive —
        opens a DB connection, reads a config file, performs I/O, does
        non-trivial computation, holds resources that must be released —
        prefer a pool checkout or close over a pre-built instance rather
        than constructing fresh on every call.
        """
        kind = getattr(cls, "__angzarr_kind__", None)
        if kind is None:
            raise BuildError(
                f"{cls.__name__} has no @command_handler / @saga / "
                f"@process_manager / @projector / @upcaster decorator — "
                f"cannot register"
            )
        self._factories.append((cls, factory))
        return self

    def build(self) -> Any:
        """Produce a typed runtime router.

        Empty → :class:`BuildError`. Mixed kinds → :class:`BuildError`.
        Homogeneous → ``CommandHandlerRouter`` / ``SagaRouter`` /
        ``ProcessManagerRouter`` / ``ProjectorRouter`` per the shared kind.
        """
        if not self._factories:
            raise BuildError(f"no handlers registered on Router {self.name!r}")

        kinds = {cls.__angzarr_kind__ for cls, _ in self._factories}
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
            UpcasterRouter,
        )
        from .validation import validate_handler

        for cls, _ in self._factories:
            validate_handler(cls)

        (kind,) = kinds
        if kind == "command_handler":
            return CommandHandlerRouter(self.name, self._factories)
        if kind == "saga":
            return SagaRouter(self.name, self._factories)
        if kind == "process_manager":
            return ProcessManagerRouter(self.name, self._factories)
        if kind == "projector":
            return ProjectorRouter(self.name, self._factories)
        if kind == "upcaster":
            return UpcasterRouter(self.name, self._factories)
        raise BuildError(f"unknown handler kind {kind!r}")
