"""gRPC servicer adapters for the unified runtime routers.

Async (``grpc.aio``) wrappers that plug a ``CommandHandlerRouter`` /
``SagaRouter`` / ``ProcessManagerRouter`` / ``ProjectorRouter`` /
``UpcasterRouter`` into the corresponding generated gRPC servicer
class. Each adapter:

  - Delegates the ``Handle`` RPC to the router's ``.dispatch`` method
    (the dispatch layer is sync compute — runs inside the async handler
    without ``await``).
  - Translates ``CommandRejectedError`` to gRPC ``FAILED_PRECONDITION``.
  - Translates ``DispatchError`` to its carried ``grpc.StatusCode``.

Audit #68 design call: async-native servicers (replaces the prior
sync ``grpcio.server`` shape). The dispatch layer stays synchronous —
it's pure compute and runs fine inside an async handler.
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
from angzarr_client.proto.angzarr.v1 import command_handler_pb2
from angzarr_client.proto.angzarr.v1 import command_handler_pb2_grpc
from angzarr_client.proto.angzarr.v1 import process_manager_pb2
from angzarr_client.proto.angzarr.v1 import process_manager_pb2_grpc
from angzarr_client.proto.angzarr.v1 import projector_pb2_grpc
from angzarr_client.proto.angzarr.v1 import saga_pb2
from angzarr_client.proto.angzarr.v1 import saga_pb2_grpc
from angzarr_client.proto.angzarr.v1 import upcaster_pb2
from angzarr_client.proto.angzarr.v1 import upcaster_pb2_grpc
from angzarr_client.proto.angzarr.v1 import types_pb2 as types

from .dispatch import DispatchError


async def _translate_and_abort(
    context: grpc.aio.ServicerContext, exc: Exception
) -> None:
    """Map a dispatch-layer exception to a gRPC status and abort the RPC.

    ``aio.ServicerContext.abort`` is a coroutine — every branch must
    ``await`` it. Mirrors the Rust client's ``client_error_to_status``
    so the same logical failure surfaces the same gRPC code on either
    side of the wire.
    """
    if isinstance(exc, CommandRejectedError):
        code_map = {
            "INVALID_ARGUMENT": grpc.StatusCode.INVALID_ARGUMENT,
            "NOT_FOUND": grpc.StatusCode.NOT_FOUND,
        }
        code = code_map.get(exc.status_code, grpc.StatusCode.FAILED_PRECONDITION)
        await context.abort(code, str(exc))
    elif isinstance(exc, DispatchError):
        await context.abort(exc.code(), exc.details())
    elif isinstance(exc, GRPCError):
        # Audit #59: GRPCError's `.code` is the SCREAMING_SNAKE id;
        # `.grpc_code` / `.grpc_details` are the gRPC StatusCode + msg.
        await context.abort(exc.grpc_code, exc.grpc_details)
    elif isinstance(exc, (InvalidArgumentError, InvalidTimestampError)):
        await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
    elif isinstance(exc, (ConnectionError, TransportError)):
        await context.abort(grpc.StatusCode.UNAVAILABLE, str(exc))
    else:
        await context.abort(grpc.StatusCode.INTERNAL, str(exc))


class CommandHandlerGrpc(command_handler_pb2_grpc.CommandHandlerServiceServicer):
    """gRPC adapter for a unified ``CommandHandlerRouter``.

    Audit #45: implements all three RPCs declared by the proto:
    ``Handle`` (always functional), ``HandleFact`` (gated on whether the
    aggregate declared any ``@handles_fact`` methods), and ``Replay``
    (gated on whether the aggregate opted in via
    ``@command_handler(supports_replay=True)``).
    """

    def __init__(self, router: Any) -> None:
        self._router = router

    async def Handle(
        self,
        request: types.ContextualCommand,
        context: grpc.aio.ServicerContext,
    ) -> command_handler_pb2.BusinessResponse:
        try:
            return self._router.dispatch(request)
        except Exception as exc:  # noqa: BLE001
            await _translate_and_abort(context, exc)
            return command_handler_pb2.BusinessResponse()

    async def HandleFact(
        self,
        request: command_handler_pb2.FactRequest,
        context: grpc.aio.ServicerContext,
    ) -> types.EventBook:
        if not self._router.supports_handle_fact():
            await context.abort(
                grpc.StatusCode.UNIMPLEMENTED,
                "no @handles_fact methods declared on registered command_handler",
            )
            return types.EventBook()
        try:
            return self._router.dispatch_fact(request)
        except Exception as exc:  # noqa: BLE001
            await _translate_and_abort(context, exc)
            return types.EventBook()

    async def Replay(
        self,
        request: command_handler_pb2.ReplayRequest,
        context: grpc.aio.ServicerContext,
    ) -> command_handler_pb2.ReplayResponse:
        if not self._router.supports_replay():
            await context.abort(
                grpc.StatusCode.UNIMPLEMENTED,
                "command_handler did not opt in via @command_handler(supports_replay=True)",
            )
            return command_handler_pb2.ReplayResponse()
        try:
            return self._router.dispatch_replay(request)
        except Exception as exc:  # noqa: BLE001
            await _translate_and_abort(context, exc)
            return command_handler_pb2.ReplayResponse()


class SagaGrpc(saga_pb2_grpc.SagaServiceServicer):
    """gRPC adapter for a unified ``SagaRouter``."""

    def __init__(self, router: Any) -> None:
        self._router = router

    async def Handle(
        self,
        request: saga_pb2.SagaHandleRequest,
        context: grpc.aio.ServicerContext,
    ) -> saga_pb2.SagaResponse:
        try:
            return self._router.dispatch(request)
        except Exception as exc:  # noqa: BLE001
            await _translate_and_abort(context, exc)
            return saga_pb2.SagaResponse()


class ProcessManagerGrpc(process_manager_pb2_grpc.ProcessManagerServiceServicer):
    """gRPC adapter for a unified ``ProcessManagerRouter``."""

    def __init__(self, router: Any) -> None:
        self._router = router

    async def Handle(
        self,
        request: process_manager_pb2.ProcessManagerHandleRequest,
        context: grpc.aio.ServicerContext,
    ) -> process_manager_pb2.ProcessManagerHandleResponse:
        try:
            return self._router.dispatch(request)
        except Exception as exc:  # noqa: BLE001
            await _translate_and_abort(context, exc)
            return process_manager_pb2.ProcessManagerHandleResponse()


class ProjectorGrpc(projector_pb2_grpc.ProjectorServiceServicer):
    """gRPC adapter for a unified ``ProjectorRouter``."""

    def __init__(self, router: Any) -> None:
        self._router = router

    async def Handle(
        self,
        request: types.EventBook,
        context: grpc.aio.ServicerContext,
    ) -> types.Projection:
        try:
            return self._router.dispatch(request)
        except Exception as exc:  # noqa: BLE001
            await _translate_and_abort(context, exc)
            return types.Projection()

    async def HandleSpeculative(
        self,
        request: types.EventBook,
        context: grpc.aio.ServicerContext,
    ) -> types.Projection:
        return await self.Handle(request, context)


class UpcasterGrpc(upcaster_pb2_grpc.UpcasterServiceServicer):
    """gRPC adapter for a unified ``UpcasterRouter``."""

    def __init__(self, router: Any) -> None:
        self._router = router

    async def Upcast(
        self,
        request: upcaster_pb2.UpcastRequest,
        context: grpc.aio.ServicerContext,
    ) -> upcaster_pb2.UpcastResponse:
        try:
            return self._router.dispatch(request)
        except Exception as exc:  # noqa: BLE001
            await _translate_and_abort(context, exc)
            return upcaster_pb2.UpcastResponse()
