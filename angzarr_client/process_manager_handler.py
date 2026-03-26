"""ProcessManagerHandler: gRPC ProcessManager servicer.

Three-phase protocol for stateful workflow coordinators:
  Replay  → rebuild typed state from PM events (called by framework)
  Prepare → declare additional destinations needed beyond the trigger
  Handle  → produce commands and process events given full context

Supports two patterns:
1. Functional: ProcessManagerHandler(name).with_replay(...).with_prepare(...).with_handle(...)
2. OO class: ProcessManagerHandler(OrderWorkflowPM)
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import grpc
from google.protobuf.any_pb2 import Any

from .process_manager import ProcessManager
from .proto.angzarr import process_manager_pb2 as pm
from .proto.angzarr import process_manager_pb2_grpc
from .proto.angzarr import types_pb2 as types
from .server import run_server

if TYPE_CHECKING:
    import structlog

# PMReplayFunc returns typed state packed in Any
PMReplayFunc = Callable[[types.EventBook], Any]
PMPrepareFunc = Callable[
    [types.EventBook, types.EventBook],
    list[types.Cover],
]
# PMHandleFunc returns ProcessManagerHandleResponse (proto-generated type)
# Third arg is destination_sequences: dict[str, int] (domain -> next sequence)
PMHandleFunc = Callable[
    [types.EventBook, types.EventBook, dict[str, int]],
    pm.ProcessManagerHandleResponse,
]


class ProcessManagerHandler(process_manager_pb2_grpc.ProcessManagerServiceServicer):
    """gRPC ProcessManager servicer.

    Process managers are stateful coordinators for long-running workflows
    across multiple aggregates. They maintain their own event-sourced state
    and react to events from multiple domains.

    Two patterns:
        ProcessManagerHandler(name).with_replay(...).with_prepare(...).with_handle(...)  # functional
        ProcessManagerHandler(OrderWorkflowPM)  # OO class
    """

    def __init__(self, name_or_class: str | type[ProcessManager]) -> None:
        if isinstance(name_or_class, type) and issubclass(
            name_or_class, ProcessManager
        ):
            # OO pattern: ProcessManager class
            self._pm_class = name_or_class
            self._name = name_or_class.name
            self._replay_fn: PMReplayFunc | None = None
            self._prepare_fn: PMPrepareFunc | None = None
            self._handle_fn: PMHandleFunc | None = None
        else:
            # Functional pattern: name string
            self._pm_class = None
            self._name = name_or_class
            self._replay_fn = None
            self._prepare_fn = None
            self._handle_fn = None

    def with_replay(self, fn: PMReplayFunc) -> ProcessManagerHandler:
        """Set the replay callback for state reconstruction."""
        self._replay_fn = fn
        return self

    def with_prepare(self, fn: PMPrepareFunc) -> ProcessManagerHandler:
        """Set the prepare callback."""
        self._prepare_fn = fn
        return self

    def with_handle(self, fn: PMHandleFunc) -> ProcessManagerHandler:
        """Set the handle callback."""
        self._handle_fn = fn
        return self

    def Replay(
        self,
        request: pm.ProcessManagerReplayRequest,
        context: grpc.ServicerContext,
    ) -> pm.ProcessManagerReplayResponse:
        """Replay PM events to rebuild typed state.

        Called by framework before Prepare/Handle to convert EventBook to typed state.
        This matches the aggregate pattern where framework handles state reconstruction.
        """
        # Custom replay takes precedence
        if self._replay_fn is not None:
            state = self._replay_fn(request.events)
            return pm.ProcessManagerReplayResponse(state=state)

        # OO pattern: use ProcessManager class's replay_state method
        if self._pm_class is not None:
            state = self._pm_class.replay_state(request.events)
            return pm.ProcessManagerReplayResponse(state=state)

        return pm.ProcessManagerReplayResponse()

    def Prepare(
        self,
        request: pm.ProcessManagerPrepareRequest,
        context: grpc.ServicerContext,
    ) -> pm.ProcessManagerPrepareResponse:
        """Phase 1: Declare additional destinations needed."""
        # Custom prepare takes precedence
        if self._prepare_fn is not None:
            destinations = self._prepare_fn(request.trigger, request.process_state)
            return pm.ProcessManagerPrepareResponse(destinations=destinations)

        # OO pattern: use ProcessManager class
        if self._pm_class is not None:
            destinations = self._pm_class.prepare_destinations(
                request.trigger, request.process_state
            )
            return pm.ProcessManagerPrepareResponse(destinations=destinations)

        return pm.ProcessManagerPrepareResponse()

    def Handle(
        self,
        request: pm.ProcessManagerHandleRequest,
        context: grpc.ServicerContext,
    ) -> pm.ProcessManagerHandleResponse:
        """Phase 2: Produce process_events, commands, and facts."""
        # Extract destination sequences from proto map
        destination_sequences = dict(request.destination_sequences)

        # Custom handle takes precedence
        if self._handle_fn is not None:
            return self._handle_fn(
                request.trigger,
                request.process_state,
                destination_sequences,
            )

        # OO pattern: use ProcessManager class (returns ProcessManagerHandleResponse)
        if self._pm_class is not None:
            return self._pm_class.handle(
                request.trigger,
                request.process_state,
                destination_sequences,
            )

        return pm.ProcessManagerHandleResponse()


def run_process_manager_server(
    handler: ProcessManagerHandler,
    default_port: str,
    logger: structlog.BoundLogger | None = None,
) -> None:
    """Start a gRPC server for a process manager.

    Args:
        handler: ProcessManagerHandler with callbacks or PM class.
        default_port: Default TCP port if PORT env not set.
        logger: Optional structlog logger.
    """
    run_server(
        process_manager_pb2_grpc.add_ProcessManagerServiceServicer_to_server,
        handler,
        service_name="ProcessManager",
        domain=handler._name,
        default_port=default_port,
        logger=logger,
    )
