"""Tests for the gRPC adapter's high-up UNIMPLEMENTED gating — audit #45.

The framework should return ``UNIMPLEMENTED`` *as high in the stack as
the metadata allows* — i.e., before any dispatch logic runs — when the
aggregate hasn't opted into HandleFact (no ``@handles_fact`` methods)
or Replay (no ``supports_replay=True`` flag).

Pure gRPC-adapter-level test: stubs the runtime router, verifies the
adapter consults ``supports_*`` and aborts before invoking
``dispatch_*``. Async-native (audit #68): the adapter methods are
``async def``, so the ``_StubContext.abort`` is a coroutine to match
``grpc.aio.ServicerContext``.
"""

from __future__ import annotations

from unittest.mock import Mock

import grpc
import pytest

from angzarr_client.proto.angzarr.v1.command_handler_pb2 import (
    FactRequest,
    ReplayRequest,
)
from angzarr_client.router.server import CommandHandlerGrpc


class _StubContext:
    """Minimal grpc.aio.ServicerContext stand-in capturing abort() calls."""

    def __init__(self) -> None:
        self.aborted: tuple[grpc.StatusCode, str] | None = None

    async def abort(self, code: grpc.StatusCode, details: str) -> None:
        self.aborted = (code, details)
        raise _Aborted()


class _Aborted(Exception):
    pass


async def test_handle_fact_returns_unimplemented_when_no_fact_handlers():
    router = Mock()
    router.supports_handle_fact.return_value = False
    adapter = CommandHandlerGrpc(router)
    ctx = _StubContext()

    with pytest.raises(_Aborted):
        await adapter.HandleFact(FactRequest(), ctx)

    assert ctx.aborted is not None
    code, details = ctx.aborted
    assert code == grpc.StatusCode.UNIMPLEMENTED
    assert "@handles_fact" in details
    router.dispatch_fact.assert_not_called()


async def test_handle_fact_dispatches_when_supported():
    router = Mock()
    router.supports_handle_fact.return_value = True
    expected = Mock()
    router.dispatch_fact.return_value = expected
    adapter = CommandHandlerGrpc(router)
    ctx = _StubContext()

    request = FactRequest()
    result = await adapter.HandleFact(request, ctx)

    assert result is expected
    assert ctx.aborted is None
    router.dispatch_fact.assert_called_once_with(request)


async def test_replay_returns_unimplemented_when_not_opted_in():
    router = Mock()
    router.supports_replay.return_value = False
    adapter = CommandHandlerGrpc(router)
    ctx = _StubContext()

    with pytest.raises(_Aborted):
        await adapter.Replay(ReplayRequest(), ctx)

    assert ctx.aborted is not None
    code, details = ctx.aborted
    assert code == grpc.StatusCode.UNIMPLEMENTED
    assert "supports_replay" in details
    router.dispatch_replay.assert_not_called()


async def test_replay_dispatches_when_opted_in():
    router = Mock()
    router.supports_replay.return_value = True
    expected = Mock()
    router.dispatch_replay.return_value = expected
    adapter = CommandHandlerGrpc(router)
    ctx = _StubContext()

    request = ReplayRequest()
    result = await adapter.Replay(request, ctx)

    assert result is expected
    assert ctx.aborted is None
    router.dispatch_replay.assert_called_once_with(request)


async def test_handle_does_not_consult_fact_or_replay_gates():
    """Sanity: the main Handle path doesn't accidentally read the new
    metadata flags. supports_*() is called only by HandleFact/Replay."""
    router = Mock()
    router.supports_handle_fact = Mock()
    router.supports_replay = Mock()
    router.dispatch.return_value = Mock()
    adapter = CommandHandlerGrpc(router)
    ctx = _StubContext()

    from angzarr_client.proto.angzarr.v1.types_pb2 import ContextualCommand

    await adapter.Handle(ContextualCommand(), ctx)

    router.supports_handle_fact.assert_not_called()
    router.supports_replay.assert_not_called()
