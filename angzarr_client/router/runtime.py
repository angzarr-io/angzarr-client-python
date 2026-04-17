"""Typed runtime routers produced by ``Router.build()``.

In R4 these are stubs carrying ``name`` + ``handlers`` only. Dispatch bodies
land in R6 (command handler), R11 (saga), R12 (process manager), R13 (projector).

No public constructors: users reach these types only through
``Router(...).build()``. Constructors are conventionally private (leading
underscore on internal factories). Tests construct through the builder.
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

S = TypeVar("S")


class _BuiltRouterBase:
    """Shared base for the four kind-specific runtime routers."""

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
