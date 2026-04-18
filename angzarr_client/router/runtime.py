"""Typed runtime routers produced by ``Router.build()``.

Five kind-specific runtime routers: command handler, saga, process manager,
projector, and upcaster. Each carries a ``name`` and a list of registered
handler instances; ``dispatch`` delegates to the matching function in
``dispatch.py``.

No public constructors: users reach these types only through
``Router(...).build()``. Constructors are conventionally private (leading
underscore on internal factories). Tests construct through the builder.
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

S = TypeVar("S")


class _BuiltRouterBase:
    """Shared base for the kind-specific runtime routers."""

    def __init__(self, name: str, handlers: list[Any]) -> None:
        self.name = name
        self.handlers = list(handlers)


class CommandHandlerRouter(_BuiltRouterBase, Generic[S]):
    """Runtime router dispatching commands to registered @command_handler instances."""

    def dispatch(self, request):
        """Route a ContextualCommand to the matching @handles method."""
        from .dispatch import dispatch_command

        return dispatch_command(self.handlers, request)


class SagaRouter(_BuiltRouterBase):
    """Runtime router dispatching events to registered @saga instances."""

    def dispatch(self, request):
        """Route a SagaHandleRequest to all matching @handles methods."""
        from .dispatch import dispatch_saga

        return dispatch_saga(self.handlers, request)


class ProcessManagerRouter(_BuiltRouterBase, Generic[S]):
    """Runtime router dispatching events to registered @process_manager instances."""

    def dispatch(self, request):
        """Route a ProcessManagerHandleRequest to matching handlers."""
        from .dispatch import dispatch_process_manager

        return dispatch_process_manager(self.handlers, request)


class ProjectorRouter(_BuiltRouterBase):
    """Runtime router fanning events out to registered @projector instances."""

    def dispatch(self, events):
        """Fan out each event in the book to matching @handles methods."""
        from .dispatch import dispatch_projector

        return dispatch_projector(self.handlers, events)


class UpcasterRouter(_BuiltRouterBase):
    """Runtime router transforming events through registered @upcaster instances."""

    def dispatch(self, request):
        """Transform each event in ``request.events`` via matching @upcasts methods."""
        from .dispatch import dispatch_upcaster

        return dispatch_upcaster(self.handlers, request)
