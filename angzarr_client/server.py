"""Common server utilities for angzarr Python examples.

Async (``grpc.aio``) gRPC server with built-in health checking and a
readiness supervisor that mirrors the Rust client's
``readiness::run_supervisor``. The supervisor flips the health status to
``SERVING`` only once every probe is passing.

Supports both TCP and Unix Domain Socket (UDS) transports.

Audit #68 design call: this module is async-native (replaces the prior
sync ``grpcio.server`` + daemon-thread supervisor). Public sync entry
points (``run_*_server``) preserve the blocking shape callers rely on
by running the asyncio event loop internally.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from dataclasses import dataclass

import grpc
import structlog
from grpc_health.v1 import health_pb2, health_pb2_grpc
from grpc_health.v1.health import aio as health_aio

from . import readiness
from .readiness import (
    BusProbe,
    OutputDomainProbe,
    Probe,
    TransportProbe,
    TransportSignal,
)


def configure_logging() -> None:
    """Configure structlog with JSON rendering and ISO timestamps."""
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(0),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )


#: Audit #77: env var name for the full TCP bind address override
#: (``host:port``). When set, supersedes the default
#: ``[::]:{port}`` composition and the ``PORT`` env resolution.
#: IPv6 hosts must include brackets (e.g. ``[::1]:50052``); IPv4
#: hosts are written bare.
ENV_BIND_ADDRESS = "ANGZARR_BIND_ADDRESS"

#: Default TCP bind host. ``"[::]"`` is the IPv6 wildcard, which on
#: Linux (``IPV6_V6ONLY=0`` by default) accepts both IPv4 (via
#: IPv4-mapped IPv6) and IPv6 connections.
DEFAULT_BIND_HOST = "[::]"


def resolve_bind_address(default_port: int = 50052) -> str:
    """Compute the TCP bind address.

    Audit #77: returns ``ANGZARR_BIND_ADDRESS`` verbatim when set,
    otherwise composes ``[::]:{port}`` where ``port`` comes from the
    ``PORT`` env var or ``default_port``.
    """
    override = os.environ.get(ENV_BIND_ADDRESS)
    if override:
        return override
    port = os.environ.get("PORT", str(default_port))
    return f"{DEFAULT_BIND_HOST}:{port}"


def get_transport_config() -> tuple[str, str]:
    """Get transport configuration from environment.

    Returns:
        Tuple of (transport_type, address)
        - For TCP: ("tcp", "[::]:{port}") — overridable via
          ``ANGZARR_BIND_ADDRESS``
        - For UDS: ("uds", "unix://{socket_path}")
    """
    transport = os.environ.get("TRANSPORT_TYPE", "tcp").lower()

    if transport == "uds":
        base_path = os.environ.get("UDS_BASE_PATH", "/tmp/angzarr")
        service_name = os.environ.get("SERVICE_NAME", "business")

        qualifier = (
            os.environ.get("DOMAIN")
            or os.environ.get("SAGA_NAME")
            or os.environ.get("PROJECTOR_NAME")
            or ""
        )

        if qualifier:
            socket_path = f"{base_path}/{service_name}-{qualifier}.sock"
        else:
            socket_path = f"{base_path}/{service_name}.sock"

        os.makedirs(os.path.dirname(socket_path), exist_ok=True)

        if os.path.exists(socket_path):
            os.remove(socket_path)

        return ("uds", f"unix:{socket_path}")

    return ("tcp", resolve_bind_address())


@dataclass
class ServerHandle:
    """Bundle returned by :func:`create_server` carrying everything the
    runner needs to start the server, drive readiness, and shut down
    cleanly. Mirrors the Rust ``server`` module's call sites.
    """

    server: grpc.aio.Server
    address: str
    transport_signal: TransportSignal
    health_servicer: health_aio.HealthServicer


def create_server(
    add_servicer_func: Callable,
    servicer: object,
    service_name: str = "",
) -> ServerHandle:
    """Create an async gRPC server with health checking wired up.

    Audit #68: health reporting starts at ``NOT_SERVING`` for both the
    empty (overall) service name and any explicit ``service_name``; the
    readiness supervisor flips it once every probe passes. Pre-#68
    Python set ``SERVING`` immediately, which made K8s readiness
    vacuously true.
    """
    transport_type, address = get_transport_config()

    server = grpc.aio.server()

    add_servicer_func(servicer, server)

    health_servicer = health_aio.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    health_servicer._server_status[""] = health_pb2.HealthCheckResponse.NOT_SERVING
    if service_name:
        health_servicer._server_status[service_name] = (
            health_pb2.HealthCheckResponse.NOT_SERVING
        )

    server.add_insecure_port(address)

    signal = TransportSignal()
    return ServerHandle(
        server=server,
        address=address,
        transport_signal=signal,
        health_servicer=health_servicer,
    )


async def _run_server_async(
    add_servicer_func: Callable,
    servicer: object,
    service_name: str,
    domain: str,
    default_port: str,
    sync_output_domains: list[str],
    has_async_outputs: bool,
    logger,
) -> None:
    """Async entry point: start the server, mark transport bound, spawn
    the readiness supervisor, and wait for termination.

    Audit #74: ``sync_output_domains`` get a per-domain
    :class:`OutputDomainProbe`. If ``has_async_outputs`` is true and
    ``ANGZARR_BUS_ENDPOINT`` is set, a single :class:`BusProbe` covers
    the async path.
    """
    if "PORT" not in os.environ:
        os.environ["PORT"] = default_port

    handle = create_server(add_servicer_func, servicer, service_name=service_name)

    transport_type = os.environ.get("TRANSPORT_TYPE", "tcp").lower()

    if logger:
        logger.info(
            "server_started",
            service=service_name,
            domain=domain,
            transport=transport_type,
            address=handle.address,
        )
    else:
        print(
            f"Server started: {service_name} ({domain}) on {handle.address} ({transport_type})"
        )

    await handle.server.start()
    handle.transport_signal.mark_bound()

    probes: list[Probe] = [TransportProbe(handle.transport_signal)]
    for sync_domain in sync_output_domains:
        probes.append(OutputDomainProbe.for_domain(sync_domain))
    if has_async_outputs:
        bus_probe = BusProbe.from_env()
        if bus_probe is not None:
            probes.append(bus_probe)

    service_names = [""]
    if service_name:
        service_names.append(service_name)

    interval, timeout = readiness.probe_config_from_env()
    supervisor_task = asyncio.create_task(
        readiness.run_supervisor(
            probes=probes,
            health_servicer=handle.health_servicer,
            service_names=service_names,
            interval=interval,
            timeout=timeout,
        )
    )

    try:
        await handle.server.wait_for_termination()
    finally:
        # Audit #83: shut down in two phases.
        # 1. Cancel the supervisor and wait for it to exit so any
        #    in-flight `health_servicer.set` finishes before we publish
        #    the final state. Only `CancelledError` is expected here;
        #    any other exception is a real bug and propagates.
        # 2. Flip every registered health name to NOT_SERVING so K8s
        #    readiness goes red and the load balancer drains the pod.
        supervisor_task.cancel()
        try:
            await supervisor_task
        except asyncio.CancelledError:
            pass
        await _publish_shutdown_status(handle.health_servicer, service_names)
        if logger:
            logger.info("server_shutdown", service=service_name, domain=domain)


async def _publish_shutdown_status(health_servicer, service_names: list[str]) -> None:
    """Audit #83: flip every registered health name to ``NOT_SERVING``
    so the K8s load balancer drains the pod. Extracted so the shutdown
    publish can be unit-tested in isolation from the runner's transport
    plumbing.
    """
    for name in service_names:
        await health_servicer.set(name, health_pb2.HealthCheckResponse.NOT_SERVING)


def run_server(
    add_servicer_func: Callable,
    servicer: object,
    service_name: str = "",
    domain: str = "",
    default_port: str = "50052",
    sync_output_domains: list[str] | None = None,
    has_async_outputs: bool = False,
    logger=None,
) -> None:
    """Run an async gRPC server until termination.

    Sync entry point — runs the asyncio event loop internally so callers
    can keep using the same blocking shape they had before.
    """
    asyncio.run(
        _run_server_async(
            add_servicer_func=add_servicer_func,
            servicer=servicer,
            service_name=service_name,
            domain=domain,
            default_port=default_port,
            sync_output_domains=sync_output_domains or [],
            has_async_outputs=has_async_outputs,
            logger=logger,
        )
    )


def cleanup_socket(socket_path: str) -> None:
    """Clean up a UDS socket file."""
    if socket_path and os.path.exists(socket_path):
        try:
            os.remove(socket_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Cross-language parity: ServerConfig + per-kind runner wrappers.
# Mirror the shapes exposed by `angzarr_client::server` on the Rust side so
# cross-language docs and examples translate directly.
# ---------------------------------------------------------------------------


@dataclass
class ServerConfig:
    """Configuration for a gRPC server.

    Cross-language alias for Rust's `ServerConfig { port, uds_path }`.
    """

    port: int = 50052
    uds_path: str | None = None

    @classmethod
    def from_env(cls, default_port: int = 50052) -> "ServerConfig":
        base_path = os.environ.get("UDS_BASE_PATH")
        service_name = os.environ.get("SERVICE_NAME")
        domain = os.environ.get("DOMAIN")
        if base_path and service_name and domain:
            socket_path = os.path.join(base_path, f"{service_name}-{domain}.sock")
            return cls(port=default_port, uds_path=socket_path)

        port_str = os.environ.get("PORT") or os.environ.get("GRPC_PORT")
        try:
            port = int(port_str) if port_str else default_port
        except ValueError:
            port = default_port
        return cls(port=port, uds_path=None)


def _run_kind_server(
    router,
    grpc_adapter_cls,
    add_servicer_fn,
    pb2_grpc_service_name: str,
    domain: str,
    default_port: int,
) -> None:
    """Shared plumbing for the per-kind ``run_*_server`` wrappers.

    Audit #74: reads ``router.sync_output_domains()`` (sync subset only)
    and ``router.has_async_outputs()``. CH / projector / upcaster
    routers default to ``[]`` and ``False``; saga / PM routers override.
    """
    servicer = grpc_adapter_cls(router)
    sync_outputs: list[str] = []
    has_async = False
    if hasattr(router, "sync_output_domains") and callable(router.sync_output_domains):
        try:
            sync_outputs = list(router.sync_output_domains())
        except Exception:  # noqa: BLE001 — best-effort introspection.
            sync_outputs = []
    if hasattr(router, "has_async_outputs") and callable(router.has_async_outputs):
        try:
            has_async = bool(router.has_async_outputs())
        except Exception:  # noqa: BLE001
            has_async = False
    run_server(
        add_servicer_fn,
        servicer,
        service_name=pb2_grpc_service_name,
        domain=domain,
        default_port=str(default_port),
        sync_output_domains=sync_outputs,
        has_async_outputs=has_async,
    )


def run_command_handler_server(
    router, domain: str = "", default_port: int = 50052
) -> None:
    """Run a command handler gRPC server."""
    from .proto.angzarr import command_handler_pb2_grpc
    from .router.server import CommandHandlerGrpc

    _run_kind_server(
        router,
        CommandHandlerGrpc,
        command_handler_pb2_grpc.add_CommandHandlerServiceServicer_to_server,
        "CommandHandlerService",
        domain,
        default_port,
    )


def run_saga_server(router, domain: str = "", default_port: int = 50052) -> None:
    """Run a saga gRPC server."""
    from .proto.angzarr import saga_pb2_grpc
    from .router.server import SagaGrpc

    _run_kind_server(
        router,
        SagaGrpc,
        saga_pb2_grpc.add_SagaServiceServicer_to_server,
        "SagaService",
        domain,
        default_port,
    )


def run_process_manager_server(
    router, domain: str = "", default_port: int = 50052
) -> None:
    """Run a process manager gRPC server."""
    from .proto.angzarr import process_manager_pb2_grpc
    from .router.server import ProcessManagerGrpc

    _run_kind_server(
        router,
        ProcessManagerGrpc,
        process_manager_pb2_grpc.add_ProcessManagerServiceServicer_to_server,
        "ProcessManagerService",
        domain,
        default_port,
    )


def run_projector_server(router, domain: str = "", default_port: int = 50052) -> None:
    """Run a projector gRPC server."""
    from .proto.angzarr import projector_pb2_grpc
    from .router.server import ProjectorGrpc

    _run_kind_server(
        router,
        ProjectorGrpc,
        projector_pb2_grpc.add_ProjectorServiceServicer_to_server,
        "ProjectorService",
        domain,
        default_port,
    )


def run_upcaster_server(router, domain: str = "", default_port: int = 50052) -> None:
    """Run an upcaster gRPC server."""
    from .proto.angzarr import upcaster_pb2_grpc
    from .router.server import UpcasterGrpc

    _run_kind_server(
        router,
        UpcasterGrpc,
        upcaster_pb2_grpc.add_UpcasterServiceServicer_to_server,
        "UpcasterService",
        domain,
        default_port,
    )
