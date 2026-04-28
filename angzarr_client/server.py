"""Common server utilities for angzarr Python examples.

Supports both TCP and Unix Domain Socket (UDS) transports for gRPC servers.
"""

import os
from collections.abc import Callable
from concurrent import futures
from dataclasses import dataclass

import grpc
import structlog
from grpc_health.v1 import health, health_pb2, health_pb2_grpc


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


def get_transport_config() -> tuple[str, str]:
    """Get transport configuration from environment.

    Returns:
        Tuple of (transport_type, address)
        - For TCP: ("tcp", "[::]:{port}")
        - For UDS: ("uds", "unix://{socket_path}")

    Environment variables:
        TRANSPORT_TYPE: "tcp" (default) or "uds"
        UDS_BASE_PATH: Base directory for sockets (default: /tmp/angzarr)
        SERVICE_NAME: Service type ("business", "saga", "projector")
        DOMAIN: Domain name for aggregates
        SAGA_NAME: Saga name (used if DOMAIN not set)
        PROJECTOR_NAME: Projector name (used if DOMAIN and SAGA_NAME not set)
    """
    transport = os.environ.get("TRANSPORT_TYPE", "tcp").lower()

    if transport == "uds":
        base_path = os.environ.get("UDS_BASE_PATH", "/tmp/angzarr")
        service_name = os.environ.get("SERVICE_NAME", "business")

        # Get the qualifier from DOMAIN, SAGA_NAME, or PROJECTOR_NAME
        qualifier = (
            os.environ.get("DOMAIN")
            or os.environ.get("SAGA_NAME")
            or os.environ.get("PROJECTOR_NAME")
            or ""
        )

        # Create socket path with optional qualifier
        if qualifier:
            socket_path = f"{base_path}/{service_name}-{qualifier}.sock"
        else:
            socket_path = f"{base_path}/{service_name}.sock"

        # Ensure parent directory exists
        os.makedirs(os.path.dirname(socket_path), exist_ok=True)

        # Remove stale socket file if exists
        if os.path.exists(socket_path):
            os.remove(socket_path)

        return ("uds", f"unix:{socket_path}")

    else:
        port = os.environ.get("PORT", "50052")
        return ("tcp", f"[::]:{port}")


def create_server(
    add_servicer_func: Callable,
    servicer: object,
    service_name: str = "",
    max_workers: int = 10,
) -> tuple[grpc.Server, str, health.HealthServicer]:
    """Create a gRPC server with health checking.

    Audit #68: health is initialized to ``NOT_SERVING`` at startup so
    the supervisor (when wired by the per-kind ``run_*_server``) can
    flip it to ``SERVING`` once probes pass. Mirrors Rust's
    ``server.rs::run_kind`` initial state. Callers that don't run a
    supervisor still see ``NOT_SERVING`` — those should construct
    without a supervisor only for narrow cases (unit tests, dev mode).

    Args:
        add_servicer_func: The add_*Servicer_to_server function
        servicer: The servicer instance
        service_name: Service name for health checking
        max_workers: Maximum thread pool workers

    Returns:
        Tuple of ``(server, address, health_servicer)``. The
        ``health_servicer`` is exposed so the per-kind runner can pass
        it to :func:`angzarr_client.readiness.run_supervisor`.
    """
    transport_type, address = get_transport_config()

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=max_workers))

    # Add the main service
    add_servicer_func(servicer, server)

    # Add health service. Initial state is NOT_SERVING for all names —
    # the supervisor flips to SERVING once probes pass (audit #68).
    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    health_servicer.set("", health_pb2.HealthCheckResponse.NOT_SERVING)
    if service_name:
        health_servicer.set(service_name, health_pb2.HealthCheckResponse.NOT_SERVING)

    # Add port/socket
    if transport_type == "uds":
        server.add_insecure_port(address)
    else:
        server.add_insecure_port(address)

    return server, address, health_servicer


def run_server(
    add_servicer_func: Callable,
    servicer: object,
    service_name: str = "",
    domain: str = "",
    default_port: str = "50052",
    logger=None,
    output_domains: list[str] | None = None,
) -> None:
    """Run a gRPC server until termination.

    Args:
        add_servicer_func: The add_*Servicer_to_server function
        servicer: The servicer instance
        service_name: Service name for logging and health checking
        domain: Domain name (for logging)
        default_port: Default TCP port if PORT env not set
        logger: Optional structlog logger
        output_domains: Audit #68 — list of downstream domains this
            server emits commands to (typically
            ``router.output_domains()`` for sagas/PMs). Each domain
            becomes an :class:`OutputDomainProbe` so health flips to
            ``SERVING`` only once every downstream coordinator is
            reachable. Empty / ``None`` means "no output probes" —
            ``TransportProbe`` alone gates readiness.
    """
    # Deferred import — readiness pulls in client.resolve_ch_endpoint
    # for OutputDomainProbe; importing at module top would create a
    # circular reference (client imports server elsewhere).
    from .readiness import (
        OutputDomainProbe,
        Probe,
        TransportProbe,
        probe_config_from_env,
        run_supervisor,
    )

    # Set default port if not specified
    if "PORT" not in os.environ:
        os.environ["PORT"] = default_port

    server, address, health_servicer = create_server(
        add_servicer_func, servicer, service_name
    )

    transport_type = os.environ.get("TRANSPORT_TYPE", "tcp").lower()

    if logger:
        logger.info(
            "server_started",
            service=service_name,
            domain=domain,
            transport=transport_type,
            address=address,
        )
    else:
        print(
            f"Server started: {service_name} ({domain}) on {address} ({transport_type})"
        )

    # Audit #68: build readiness probes — TransportProbe (one-shot,
    # flipped after server.start) + one OutputDomainProbe per declared
    # output domain. Health stays NOT_SERVING until every probe passes.
    transport_probe, transport_signal = TransportProbe.new()
    probes: list[Probe] = [transport_probe]
    for out_domain in output_domains or []:
        probes.append(OutputDomainProbe.for_domain(out_domain))

    interval, timeout = probe_config_from_env()
    service_names = [""]
    if service_name:
        service_names.append(service_name)
    supervisor = run_supervisor(
        probes,
        health_servicer,
        service_names,
        interval,
        timeout,
    )

    server.start()
    transport_signal.mark_bound()
    try:
        server.wait_for_termination()
    finally:
        supervisor.stop()


def cleanup_socket(socket_path: str) -> None:
    """Clean up a UDS socket file.

    Args:
        socket_path: Path to the socket file to remove
    """
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
    Construct from environment via :meth:`from_env` or instantiate directly.
    """

    port: int = 50052
    """Port to listen on (TCP mode)."""

    uds_path: str | None = None
    """Unix domain socket path (UDS mode). When set, takes precedence over port."""

    @classmethod
    def from_env(cls, default_port: int = 50052) -> "ServerConfig":
        """Read config from environment variables.

        UDS mode (standalone) when `UDS_BASE_PATH`, `SERVICE_NAME`, and `DOMAIN`
        are all set: socket path becomes `{UDS_BASE_PATH}/{SERVICE_NAME}-{DOMAIN}.sock`.

        Otherwise TCP mode with port read from `PORT` or `GRPC_PORT`, falling
        back to `default_port`.
        """
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

    Audit #68: reads ``router.output_domains()`` so the readiness
    supervisor can construct one ``OutputDomainProbe`` per declared
    target. CH and projector routers return ``[]`` (no outbound
    destinations); saga and PM routers return their target domain
    list.
    """
    servicer = grpc_adapter_cls(router)
    output_domains = []
    if hasattr(router, "output_domains") and callable(router.output_domains):
        try:
            output_domains = list(router.output_domains())
        except Exception:  # noqa: BLE001 — best-effort introspection.
            output_domains = []
    run_server(
        add_servicer_fn,
        servicer,
        service_name=pb2_grpc_service_name,
        domain=domain,
        default_port=str(default_port),
        output_domains=output_domains,
    )


def run_command_handler_server(
    router, domain: str = "", default_port: int = 50052
) -> None:
    """Run a command handler gRPC server. Mirrors Rust's `run_command_handler_server`."""
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
    """Run a saga gRPC server. Mirrors Rust's `run_saga_server`."""
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
    """Run a process manager gRPC server. Mirrors Rust's `run_process_manager_server`."""
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
    """Run a projector gRPC server. Mirrors Rust's `run_projector_server`."""
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
    """Run an upcaster gRPC server. Mirrors Rust's `run_upcaster_server`."""
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
