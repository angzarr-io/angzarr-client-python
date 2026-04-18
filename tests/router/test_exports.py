"""R15 (partial): verify the unified router public surface is reachable.

The unified router lives at ``angzarr_client.router`` during the Phase 1
cutover. Users import the full API from there. Top-level re-exports on
``angzarr_client`` are deferred to the full R15 cutover (which also removes
the old OO bases and their top-level ``handles`` / ``applies`` names).

This test freezes the shape of the public surface so later changes don't
silently break it.
"""

from __future__ import annotations


def test_router_package_exposes_builder_and_error():
    from angzarr_client.router import BuildError, Router

    assert Router is not None
    assert BuildError is not None


def test_router_package_exposes_class_decorators():
    from angzarr_client.router import (
        command_handler,
        process_manager,
        projector,
        saga,
        upcaster,
    )

    assert callable(command_handler)
    assert callable(saga)
    assert callable(process_manager)
    assert callable(projector)
    assert callable(upcaster)


def test_router_package_exposes_method_decorators():
    from angzarr_client.router import applies, handles, rejected, state_factory, upcasts

    assert callable(handles)
    assert callable(applies)
    assert callable(rejected)
    assert callable(state_factory)
    assert callable(upcasts)


def test_runtime_router_types_reachable():
    from angzarr_client.router.runtime import (
        CommandHandlerRouter,
        ProcessManagerRouter,
        ProjectorRouter,
        SagaRouter,
        UpcasterRouter,
    )

    assert CommandHandlerRouter is not None
    assert SagaRouter is not None
    assert ProcessManagerRouter is not None
    assert ProjectorRouter is not None
    assert UpcasterRouter is not None


def test_grpc_adapters_reachable():
    from angzarr_client.router.server import (
        CommandHandlerGrpc,
        ProcessManagerGrpc,
        ProjectorGrpc,
        SagaGrpc,
        UpcasterGrpc,
    )

    assert CommandHandlerGrpc is not None
    assert SagaGrpc is not None
    assert ProcessManagerGrpc is not None
    assert ProjectorGrpc is not None
    assert UpcasterGrpc is not None


def test_dispatch_error_is_grpc_rpc_error():
    import grpc
    from angzarr_client.router.dispatch import DispatchError

    err = DispatchError(grpc.StatusCode.INVALID_ARGUMENT, "bad input")
    assert isinstance(err, grpc.RpcError)
    assert err.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert err.details() == "bad input"
