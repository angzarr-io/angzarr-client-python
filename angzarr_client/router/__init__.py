"""Unified Router — public surface for handler registration and dispatch."""

from .builder import BuildError, Router
from .decorators import (
    applies,
    command_handler,
    handles,
    handles_fact,
    process_manager,
    projector,
    rejected,
    saga,
    state_factory,
    upcaster,
    upcasts,
)
from .dispatch import DispatchError
from .responses import (
    ProcessManagerResponse,
    RejectionHandlerResponse,
    SagaHandlerResponse,
)
from .runtime import (
    CommandHandlerRouter,
    ProcessManagerRouter,
    ProjectorRouter,
    SagaRouter,
    UpcasterRouter,
)

__all__ = [
    # Builder
    "Router",
    "BuildError",
    "DispatchError",
    # Class decorators
    "command_handler",
    "saga",
    "process_manager",
    "projector",
    "upcaster",
    # Method decorators
    "handles",
    "handles_fact",
    "applies",
    "rejected",
    "state_factory",
    "upcasts",
    # Response types
    "SagaHandlerResponse",
    "ProcessManagerResponse",
    "RejectionHandlerResponse",
    # Runtime router types (typed return from Router.build())
    "CommandHandlerRouter",
    "SagaRouter",
    "ProcessManagerRouter",
    "ProjectorRouter",
    "UpcasterRouter",
]
