"""Environment-driven settings for component processes: logging and the
transport (TCP address or Unix socket) a component host binds. The host
itself is :class:`angzarr_client.host.ComponentHost`."""

from __future__ import annotations

import os

import structlog


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
