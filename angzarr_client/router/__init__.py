"""Unified Router — Tier 5 Phase 1 (Python reference implementation).

Package name is transitional: kept separate from the existing ``router.py``
module during R1–R14. In R15, the old flat ``router.py`` is deleted and this
package is renamed to ``router`` to become the public surface.

Do not import directly from ``angzarr_client.router`` in user code; wait
for the R15 cutover when the package moves to its final location.
"""

from .builder import BuildError, Router
from .decorators import (
    applies,
    command_handler,
    handles,
    process_manager,
    projector,
    rejected,
    saga,
    state_factory,
    upcaster,
    upcasts,
)
from .dispatch import DispatchError
from .responses import ProcessManagerResponse, SagaHandlerResponse
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
    "applies",
    "rejected",
    "state_factory",
    "upcasts",
    # Response types
    "SagaHandlerResponse",
    "ProcessManagerResponse",
    # Runtime router types (typed return from Router.build())
    "CommandHandlerRouter",
    "SagaRouter",
    "ProcessManagerRouter",
    "ProjectorRouter",
    "UpcasterRouter",
]
