"""gRPC servicer adapters for the unified runtime routers.

Thin wrappers that plug a ``CommandHandlerRouter`` / ``SagaRouter`` /
``ProcessManagerRouter`` / ``ProjectorRouter`` / ``UpcasterRouter`` into
the corresponding generated gRPC servicer class. Each adapter:

  - Delegates the ``Handle`` RPC to the router's ``.dispatch`` method.
  - Translates ``CommandRejectedError`` to gRPC ``FAILED_PRECONDITION``.
  - Translates ``DispatchError`` to its carried ``grpc.StatusCode``.

These adapters live alongside the unified router and are the recommended
way to serve the new routers over gRPC. The legacy handlers in
``aggregate_handler.py`` / ``saga_handler.py`` / ``process_manager_handler.py``
/ ``projector_handler.py`` continue to function unchanged until R15.
"""

from __future__ import annotations

from typing import Any

import grpc

from angzarr_client.errors import (
    CommandRejectedError,
    ConnectionError,
    GRPCError,
    InvalidArgumentError,
    InvalidTimestampError,
    TransportError,
)
from angzarr_client.proto.angzarr import (
    command_handler_pb2,
    command_handler_pb2_grpc,
    process_manager_pb2,
    process_manager_pb2_grpc,
    projector_pb2_grpc,
    saga_pb2,
    saga_pb2_grpc,
    upcaster_pb2,
    upcaster_pb2_grpc,
)
from angzarr_client.proto.angzarr import types_pb2 as types

from .dispatch import DispatchError


def _translate_and_abort(context: grpc.ServicerContext, exc: Exception) -> None:
    """Map a dispatch-layer exception to a gRPC status and abort the RPC.

    Mirrors the Rust client's ``client_error_to_status`` so the same logical
    failure surfaces the same gRPC code on either side of the wire.
    """
    if isinstance(exc, CommandRejectedError):
        # Honor the rejection's own status_code rather than collapsing to
        # FAILED_PRECONDITION — INVALID_ARGUMENT and NOT_FOUND rejections
        # carry retry-stopping semantics that callers rely on.
        code_map = {
            "INVALID_ARGUMENT": grpc.StatusCode.INVALID_ARGUMENT,
            "NOT_FOUND": grpc.StatusCode.NOT_FOUND,
        }
        code = code_map.get(exc.status_code, grpc.StatusCode.FAILED_PRECONDITION)
        context.abort(code, str(exc))
    elif isinstance(exc, DispatchError):
        context.abort(exc.code(), exc.details())
    elif isinstance(exc, GRPCError):
        # Pass the upstream gRPC status through unchanged.
        # Audit #59: GRPCError's `.code` is the SCREAMING_SNAKE id;
        # `.grpc_code` / `.grpc_details` are the gRPC StatusCode + msg.
        context.abort(exc.grpc_code, exc.grpc_details)
    elif isinstance(exc, (InvalidArgumentError, InvalidTimestampError)):
        context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
    elif isinstance(exc, (ConnectionError, TransportError)):
        context.abort(grpc.StatusCode.UNAVAILABLE, str(exc))
    else:
        # Unknown exceptions bubble up as INTERNAL.
        context.abort(grpc.StatusCode.INTERNAL, str(exc))


class CommandHandlerGrpc(command_handler_pb2_grpc.CommandHandlerServiceServicer):
    """gRPC adapter for a unified ``CommandHandlerRouter``.

    Audit #45: implements all three RPCs declared by the proto:
    ``Handle`` (always functional), ``HandleFact`` (gated on whether the
    aggregate declared any ``@handles_fact`` methods), and ``Replay``
    (gated on whether the aggregate opted in via
    ``@command_handler(supports_replay=True)``).

    Both ``HandleFact`` and ``Replay`` short-circuit to ``UNIMPLEMENTED``
    based on a pure metadata read on the runtime router — no dispatch
    work runs for aggregates that didn't opt in. Per the proto's
    Optional contract, the coordinator handles the fallback (pass-
    through-persist for facts; degrade to ``MERGE_STRICT`` for replay).
    """

    def __init__(self, router: Any) -> None:
        self._router = router

    def Handle(
        self,
        request: types.ContextualCommand,
        context: grpc.ServicerContext,
    ) -> command_handler_pb2.BusinessResponse:
        return self._dispatch(request, context)

    def HandleFact(
        self,
        request: command_handler_pb2.FactRequest,
        context: grpc.ServicerContext,
    ) -> types.EventBook:
        # Audit #45: gate as high in the stack as the metadata allows.
        # No fact-handlers declared → return UNIMPLEMENTED before any
        # dispatch logic runs. Coordinator's pass-through-persist
        # fallback handles persistence.
        if not self._router.supports_handle_fact():
            context.abort(
                grpc.StatusCode.UNIMPLEMENTED,
                "no @handles_fact methods declared on registered command_handler",
            )
            return types.EventBook()  # unreachable; abort raises
        try:
            return self._router.dispatch_fact(request)
        except Exception as exc:  # noqa: BLE001 — translated via _translate_and_abort
            _translate_and_abort(context, exc)
            return types.EventBook()

    def Replay(
        self,
        request: command_handler_pb2.ReplayRequest,
        context: grpc.ServicerContext,
    ) -> command_handler_pb2.ReplayResponse:
        # Audit #45: same shape as HandleFact — gate on metadata,
        # short-circuit if the aggregate didn't opt in.
        if not self._router.supports_replay():
            context.abort(
                grpc.StatusCode.UNIMPLEMENTED,
                "command_handler did not opt in via @command_handler(supports_replay=True)",
            )
            return command_handler_pb2.ReplayResponse()
        try:
            return self._router.dispatch_replay(request)
        except Exception as exc:  # noqa: BLE001
            _translate_and_abort(context, exc)
            return command_handler_pb2.ReplayResponse()

    def _dispatch(
        self, request: types.ContextualCommand, context: grpc.ServicerContext
    ) -> command_handler_pb2.BusinessResponse:
        try:
            return self._router.dispatch(request)
        except Exception as exc:  # noqa: BLE001 — translated via _translate_and_abort
            _translate_and_abort(context, exc)
            return command_handler_pb2.BusinessResponse()


class SagaGrpc(saga_pb2_grpc.SagaServiceServicer):
    """gRPC adapter for a unified ``SagaRouter``."""

    def __init__(self, router: Any) -> None:
        self._router = router

    def Handle(
        self, request: saga_pb2.SagaHandleRequest, context: grpc.ServicerContext
    ) -> saga_pb2.SagaResponse:
        try:
            return self._router.dispatch(request)
        except Exception as exc:  # noqa: BLE001
            _translate_and_abort(context, exc)
            return saga_pb2.SagaResponse()


class ProcessManagerGrpc(process_manager_pb2_grpc.ProcessManagerServiceServicer):
    """gRPC adapter for a unified ``ProcessManagerRouter``."""

    def __init__(self, router: Any) -> None:
        self._router = router

    def Handle(
        self,
        request: process_manager_pb2.ProcessManagerHandleRequest,
        context: grpc.ServicerContext,
    ) -> process_manager_pb2.ProcessManagerHandleResponse:
        try:
            return self._router.dispatch(request)
        except Exception as exc:  # noqa: BLE001
            _translate_and_abort(context, exc)
            return process_manager_pb2.ProcessManagerHandleResponse()


class ProjectorGrpc(projector_pb2_grpc.ProjectorServiceServicer):
    """gRPC adapter for a unified ``ProjectorRouter``."""

    def __init__(self, router: Any) -> None:
        self._router = router

    def Handle(
        self, request: types.EventBook, context: grpc.ServicerContext
    ) -> types.Projection:
        try:
            return self._router.dispatch(request)
        except Exception as exc:  # noqa: BLE001
            _translate_and_abort(context, exc)
            return types.Projection()

    def HandleSpeculative(
        self, request: types.EventBook, context: grpc.ServicerContext
    ) -> types.Projection:
        return self.Handle(request, context)


class UpcasterGrpc(upcaster_pb2_grpc.UpcasterServiceServicer):
    """gRPC adapter for a unified ``UpcasterRouter``."""

    def __init__(self, router: Any) -> None:
        self._router = router

    def Upcast(
        self,
        request: upcaster_pb2.UpcastRequest,
        context: grpc.ServicerContext,
    ) -> upcaster_pb2.UpcastResponse:
        try:
            return self._router.dispatch(request)
        except Exception as exc:  # noqa: BLE001
            _translate_and_abort(context, exc)
            return upcaster_pb2.UpcastResponse()
