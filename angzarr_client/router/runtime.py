"""Typed runtime routers produced by ``Router.build()``.

Five kind-specific runtime routers: command handler, saga, process manager,
projector, and upcaster. Each carries a ``name`` and a list of
``(cls, factory)`` pairs; ``dispatch`` delegates to the matching function
in ``dispatch.py``, which calls ``factory()`` per request to obtain a
fresh handler instance.

No public constructors: users reach these types only through
``Router(...).build()``. Constructors are conventionally private (leading
underscore on internal factories). Tests construct through the builder.
"""

from __future__ import annotations

from typing import Any, Callable, Generic, TypeVar

S = TypeVar("S")

Factory = tuple[type, Callable[[], Any]]


class _BuiltRouterBase:
    """Shared base for the five kind-specific runtime routers.

    Holds ``(cls, factory)`` pairs rather than handler instances so each
    dispatch call can obtain a fresh (or pooled) handler. This gives the
    router safe reuse across threads — shared mutable state on a handler
    instance would otherwise race between concurrent dispatch calls.
    """

    def __init__(self, factories: list[Factory]) -> None:
        self._factories: list[Factory] = list(factories)

    def _first_meta(self) -> dict:
        """First registered handler's ``__angzarr_meta__`` (or ``{}``)."""
        if not self._factories:
            return {}
        cls, _ = self._factories[0]
        return getattr(cls, "__angzarr_meta__", {}) or {}

    @property
    def name(self) -> str:
        """Router name read from the first registered handler's metadata.

        Audit finding #42 (single source of truth — first handler's
        ``__angzarr_meta__["name"]`` wins). Mirrors Rust's
        ``runtime.rs:642+`` per-kind ``name()`` accessors. CH overrides
        to read ``domain`` instead, since ``@command_handler`` has no
        ``name`` attribute.
        """
        return self._first_meta().get("name", "")

    def handler_count(self) -> int:
        """Number of factories registered on this runtime router.

        Mirrors Rust's `handler_count()` exposed on every kind-specific
        router via the `impl_handler_count!` macro
        (`runtime.rs:454-468`). P3.2 / audit finding.
        """
        return len(self._factories)

    def __len__(self) -> int:
        """Pythonic alias for :meth:`handler_count`. ``len(router)``."""
        return self.handler_count()

    def output_domains(self) -> list[str]:
        """Domains this router emits commands to.

        Read from each registered handler class's ``__angzarr_meta__``.
        Default is empty — only sagas (``target``) and process managers
        (``targets``) override.
        """
        return []


class CommandHandlerRouter(_BuiltRouterBase, Generic[S]):
    """Runtime router dispatching commands to registered @command_handler instances."""

    @property
    def name(self) -> str:
        """Domain this router serves (read from the first registered
        handler's ``@command_handler(domain=...)`` metadata).

        ``@command_handler`` has no ``name`` attribute, so the domain is
        the natural identifier. Mirrors Rust's
        ``CommandHandlerRouter::name()`` (``runtime.rs:625-633``).
        """
        return self._first_meta().get("domain", "")

    def dispatch(self, request):
        """Route a ContextualCommand to the matching @handles method."""
        from .dispatch import dispatch_command

        return dispatch_command(self._factories, request)


class SagaRouter(_BuiltRouterBase):
    """Runtime router dispatching events to registered @saga instances."""

    def dispatch(self, request):
        """Route a SagaHandleRequest to all matching @handles methods."""
        from .dispatch import dispatch_saga

        return dispatch_saga(self._factories, request)

    def output_domains(self) -> list[str]:
        seen: list[str] = []
        for cls, _factory in self._factories:
            meta = getattr(cls, "__angzarr_meta__", {})
            target = meta.get("target")
            if target and target not in seen:
                seen.append(target)
        return seen


class ProcessManagerRouter(_BuiltRouterBase, Generic[S]):
    """Runtime router dispatching events to registered @process_manager instances."""

    def dispatch(self, request):
        """Route a ProcessManagerHandleRequest to matching handlers."""
        from .dispatch import dispatch_process_manager

        return dispatch_process_manager(self._factories, request)

    def output_domains(self) -> list[str]:
        seen: list[str] = []
        for cls, _factory in self._factories:
            meta = getattr(cls, "__angzarr_meta__", {})
            for target in meta.get("targets", []):
                if target and target not in seen:
                    seen.append(target)
        return seen


class ProjectorRouter(_BuiltRouterBase):
    """Runtime router fanning events out to registered @projector instances."""

    def dispatch(self, events):
        """Fan out each event in the book to matching @handles methods."""
        from .dispatch import dispatch_projector

        return dispatch_projector(self._factories, events)


class UpcasterRouter(_BuiltRouterBase):
    """Runtime router transforming events through registered @upcaster instances."""

    def dispatch(self, request):
        """Transform each event in ``request.events`` via matching @upcasts methods."""
        from .dispatch import dispatch_upcaster

        return dispatch_upcaster(self._factories, request)
