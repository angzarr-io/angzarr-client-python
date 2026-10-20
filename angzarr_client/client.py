"""Client implementations for Angzarr gRPC services."""

from __future__ import annotations

import os
from enum import Enum
from typing import TYPE_CHECKING
from uuid import UUID as PyUUID

import grpc

from ._args import _as_uuid
from .errors import GRPCError, TransportError
from .helpers import correlated_metadata, uuid_to_proto
from .retry import RetryPolicy, default_retry_policy

if TYPE_CHECKING:
    from typing_extensions import Self

    from .builder import CommandBuilder, QueryBuilder
from ._pb import (
    CommandBook,
    CommandHandlerCoordinatorServiceStub,
    CommandResponse,
    CorrelationQuery,
    EventBook,
    EventQueryServiceStub,
    Query,
)


class TransportMode(Enum):
    """Transport mode for gRPC connections."""

    STANDALONE = "standalone"  # Unix Domain Sockets
    DISTRIBUTED = "distributed"  # TCP via K8s DNS


def resolve_ch_endpoint(
    domain: str,
    mode: TransportMode | None = None,
    *,
    uds_base: str = "/tmp/angzarr",
    namespace: str = "angzarr",
    port: int = 1310,
) -> str:
    """Resolve domain to command handler coordinator endpoint.

    Args:
        domain: The domain name
        mode: Transport mode. If None, detected from ANGZARR_MODE env var.
        uds_base: Base path for Unix Domain Sockets (standalone mode)
        namespace: Kubernetes namespace (distributed mode)
        port: gRPC port (distributed mode)

    Returns:
        Endpoint string suitable for _create_channel:
        - Standalone: /tmp/angzarr/ch-<domain>.sock
        - Distributed: ch-<domain>.angzarr.svc:1310

    Environment Variables:
        ANGZARR_MODE: "standalone" or "distributed" (default: "distributed")
        ANGZARR_UDS_BASE: Override uds_base (default: /tmp/angzarr)
        ANGZARR_NAMESPACE: Override namespace (default: angzarr)
        ANGZARR_CH_PORT: Override port (default: 1310)
    """
    if mode is None:
        mode_str = os.environ.get("ANGZARR_MODE", "distributed")
        mode = TransportMode(mode_str)

    if mode == TransportMode.STANDALONE:
        base = os.environ.get("ANGZARR_UDS_BASE", uds_base)
        return f"{base}/ch-{domain}.sock"
    else:
        ns = os.environ.get("ANGZARR_NAMESPACE", namespace)
        p = int(os.environ.get("ANGZARR_CH_PORT", str(port)))
        return f"ch-{domain}.{ns}.svc:{p}"


def _create_channel(endpoint: str) -> grpc.Channel:
    """Create a gRPC channel for the given endpoint.

    Supports both TCP (host:port) and Unix Domain Sockets (file paths).
    UDS endpoints are converted to ``unix:`` URIs following the gRPC
    name-resolution spec, with the leading prefix preserved:

      - absolute path ``/abs/path`` → ``unix:///abs/path`` (empty authority)
      - relative path ``./rel/path`` → ``unix:./rel/path`` (no authority,
        leading ``./`` retained for the gRPC URI resolver)
      - already-prefixed ``unix:...`` strings → passed through unchanged
      - ``http://host:port`` → an insecure channel to ``host:port``
      - ``https://host:port`` → a TLS channel (system roots) to ``host:port``
      - everything else → treated as TCP host:port

    The Go/Java/C++ clients emit the same forms. Rust uses a custom connector
    with the raw path. C# uses ``SocketsHttpHandler`` with ``UnixDomainSocketEndPoint``.

    Cross-language interop note: grpc-python 1.57+ has a UDS interop issue with
    tonic (Rust) servers — "RST_STREAM with error code 1" (PROTOCOL_ERROR).
    See hyperium/tonic#826, #742 and grpc/grpc#34760. Use TCP when crossing
    grpc-python <-> tonic.
    """
    if endpoint.startswith("./"):
        return grpc.insecure_channel(f"unix:{endpoint}")
    elif endpoint.startswith("/"):
        return grpc.insecure_channel(f"unix://{endpoint}")
    elif endpoint.startswith("unix:"):
        return grpc.insecure_channel(endpoint)
    elif endpoint.startswith("https://"):
        return grpc.secure_channel(
            endpoint[len("https://") :].rstrip("/"), grpc.ssl_channel_credentials()
        )
    elif endpoint.startswith("http://"):
        return grpc.insecure_channel(endpoint[len("http://") :].rstrip("/"))
    else:
        return grpc.insecure_channel(endpoint)


# grpc-python raises ``ValueError`` with this message when an RPC is
# invoked on a channel that has been closed.
_CLOSED_CHANNEL_MESSAGE = "Cannot invoke RPC on closed channel!"


def _invoke(rpc, request, *, timeout: float | None, metadata, stream: bool = False):
    """Invoke a stub method, mapping transport failures to client errors.

    A gRPC status becomes :class:`GRPCError`; an RPC on a closed channel
    becomes :class:`TransportError` (a connection error). Streaming
    responses are drained so their status surfaces here.
    """
    try:
        response = rpc(request, timeout=timeout, metadata=metadata)
        return list(response) if stream else response
    except grpc.RpcError as e:
        raise GRPCError(e) from e
    except ValueError as e:
        if str(e) == _CLOSED_CHANNEL_MESSAGE:
            raise TransportError(e, details={"reason": "channel closed"}) from e
        raise


class QueryClient:
    """Client for the EventQueryService."""

    def __init__(self, channel: grpc.Channel, owns_channel: bool = True):
        self._stub = EventQueryServiceStub(channel)
        self._channel = channel
        self._owns_channel = owns_channel

    @classmethod
    def connect(cls, endpoint: str, retry: RetryPolicy | None = None) -> QueryClient:
        """Connect to an event query service at the given endpoint.

        Args:
            endpoint: The gRPC endpoint (host:port or UDS path).
            retry: Optional retry policy. Defaults to exponential backoff.
        """
        policy = retry or default_retry_policy()
        channel = policy.execute(lambda: _create_channel(endpoint))
        return cls(channel, owns_channel=True)

    @classmethod
    def from_channel(cls, channel: grpc.Channel) -> QueryClient:
        """Create a client from a caller-managed channel.

        The returned client will not close the channel when `close()` is called;
        the caller retains ownership.
        """
        return cls(channel, owns_channel=False)

    @classmethod
    def from_env(cls, env_var: str, default: str) -> QueryClient:
        """Connect using an environment variable with fallback."""
        endpoint = os.environ.get(env_var) or default
        return cls.connect(endpoint)

    @classmethod
    def from_stub(cls, stub) -> QueryClient:
        """Compose a QueryClient from a pre-built gRPC stub.

        Bypasses channel construction so tests can inject a fake stub
        (see `tests/_fakes.py::RecordingStub`) without opening a
        socket. The returned client does not own a channel; ``close()``
        is a no-op (PARITY_AUDIT.md plan item P1.12.e / finding #24).
        """
        instance = cls.__new__(cls)
        instance._stub = stub
        instance._channel = None
        instance._owns_channel = False
        return instance

    def get_event_book(self, query: Query, timeout: float | None = None) -> EventBook:
        """Retrieve a single EventBook for the query.

        Args:
            query: The query specification.
            timeout: Optional per-call deadline in seconds.

        Audit #69 stage (b): attaches ``x-correlation-id`` metadata from
        ``query.correlation_id`` so OTel exporters / mesh sidecars
        can filter on it without decoding the body. Send-only.
        """
        md = correlated_metadata(query.correlation_id)
        return _invoke(self._stub.GetEventBook, query, timeout=timeout, metadata=md)

    def get_events(self, query: Query, timeout: float | None = None) -> list[EventBook]:
        """Retrieve all EventBooks matching the query.

        Args:
            query: The query specification.
            timeout: Optional per-call deadline in seconds.
        """
        md = correlated_metadata(query.correlation_id)
        return _invoke(
            self._stub.GetEvents, query, timeout=timeout, metadata=md, stream=True
        )

    def get_correlated_events(
        self, correlation_id: PyUUID | str, timeout: float | None = None
    ) -> list[EventBook]:
        """Every event of one correlation, across all domains
        (EventQueryService.GetCorrelatedEvents): one EventBook per stream
        holding that stream's correlated pages, in sequence order. A
        correlation with no events answers an empty list.

        Args:
            correlation_id: The correlation, as a UUID or its text form.
            timeout: Optional per-call deadline in seconds.
        """
        request = CorrelationQuery()
        request.correlation_id.CopyFrom(
            uuid_to_proto(_as_uuid(correlation_id, "correlation_id"))
        )
        md = correlated_metadata(request.correlation_id)
        return _invoke(
            self._stub.GetCorrelatedEvents,
            request,
            timeout=timeout,
            metadata=md,
            stream=True,
        )

    def query(self, domain: str, root: PyUUID) -> QueryBuilder:
        """Start building a query for a specific aggregate."""
        from .builder import QueryBuilder

        return QueryBuilder(self, domain, root)

    def query_domain(self, domain: str) -> QueryBuilder:
        """Start building a query by domain only (use with by_correlation_id)."""
        from .builder import QueryBuilder

        return QueryBuilder(self, domain)

    def close(self) -> None:
        """Close the underlying channel if this client owns it."""
        if self._owns_channel:
            self._channel.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


class CommandHandlerClient:
    """Client for the CommandHandlerCoordinatorService."""

    def __init__(self, channel: grpc.Channel, owns_channel: bool = True):
        self._stub = CommandHandlerCoordinatorServiceStub(channel)
        self._channel = channel
        self._owns_channel = owns_channel

    @classmethod
    def connect(
        cls, endpoint: str, retry: RetryPolicy | None = None
    ) -> CommandHandlerClient:
        """Connect to a command handler coordinator at the given endpoint.

        Args:
            endpoint: The gRPC endpoint (host:port or UDS path).
            retry: Optional retry policy. Defaults to exponential backoff.
        """
        policy = retry or default_retry_policy()
        channel = policy.execute(lambda: _create_channel(endpoint))
        return cls(channel, owns_channel=True)

    @classmethod
    def from_channel(cls, channel: grpc.Channel) -> CommandHandlerClient:
        """Create a client from a caller-managed channel.

        The returned client will not close the channel when `close()` is called;
        the caller retains ownership.
        """
        return cls(channel, owns_channel=False)

    @classmethod
    def from_env(cls, env_var: str, default: str) -> CommandHandlerClient:
        """Connect using an environment variable with fallback."""
        endpoint = os.environ.get(env_var) or default
        return cls.connect(endpoint)

    @classmethod
    def from_stub(cls, stub) -> CommandHandlerClient:
        """Compose a CommandHandlerClient from a pre-built gRPC stub.

        See :meth:`QueryClient.from_stub` for the test-only seam used
        to inject `tests/_fakes.py::RecordingStub`. P1.12.e / finding #24.
        """
        instance = cls.__new__(cls)
        instance._stub = stub
        instance._channel = None
        instance._owns_channel = False
        return instance

    def handle(
        self, command: CommandBook, timeout: float | None = None
    ) -> CommandResponse:
        """Send a command and wait for its decision
        (CommandHandlerCoordinatorService.Handle, DECISION): the response
        carries the persisted events; a rejection raises :class:`GRPCError`.

        Args:
            command: The command to execute.
            timeout: Optional per-call deadline in seconds.

        Audit #69 stage (b): attaches ``x-correlation-id`` metadata
        from ``command.page.correlation_id``.
        """
        md = correlated_metadata(command.page.correlation_id)
        return _invoke(self._stub.Handle, command, timeout=timeout, metadata=md)

    def handle_async(self, command: CommandBook, timeout: float | None = None) -> None:
        """Send a command without waiting for its decision
        (CommandHandlerCoordinatorService.HandleAsync, ASYNC): the coordinator
        validates it, assigns its ids and enqueues it. A malformed command
        raises :class:`GRPCError`; a later rejection has no caller to
        answer, so follow the command's correlation id for its outcome."""
        md = correlated_metadata(command.page.correlation_id)
        _invoke(self._stub.HandleAsync, command, timeout=timeout, metadata=md)

    def handle_sync(
        self, command: CommandBook, timeout: float | None = None
    ) -> CommandResponse:
        """Send a command and wait for its decision and the synchronous
        projectors (CommandHandlerCoordinatorService.HandleSync, SIMPLE): the
        response carries the persisted events, and ``outcome.projections``
        (with ``outcome.projector_failures``) the projectors' results."""
        md = correlated_metadata(command.page.correlation_id)
        return _invoke(self._stub.HandleSync, command, timeout=timeout, metadata=md)

    def command(self, domain: str, root: PyUUID) -> CommandBuilder:
        """Start building a command for an existing aggregate."""
        from .builder import CommandBuilder

        return CommandBuilder(self, domain, root)

    def command_new(self, domain: str) -> CommandBuilder:
        """Start building a command for a new aggregate.

        Materializes a fresh UUID v4 client-side and passes it
        explicitly to CommandBuilder. Audit #67: ``root`` is always
        client-assigned, no path exists to skip it.
        """
        from uuid import uuid4

        from .builder import CommandBuilder

        return CommandBuilder(self, domain, uuid4())

    def close(self) -> None:
        """Close the underlying channel if this client owns it."""
        if self._owns_channel:
            self._channel.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


class DomainClient:
    """Combined client for command handler and query operations on a single domain."""

    def __init__(self, channel: grpc.Channel, owns_channel: bool = True):
        self.command_handler = CommandHandlerClient.from_channel(channel)
        self.query = QueryClient.from_channel(channel)
        self._channel = channel
        self._owns_channel = owns_channel

    @classmethod
    def connect(cls, endpoint: str, retry: RetryPolicy | None = None) -> DomainClient:
        """Connect to a domain's coordinator at the given endpoint.

        Args:
            endpoint: The gRPC endpoint (host:port or UDS path).
            retry: Optional retry policy. Defaults to exponential backoff.
        """
        policy = retry or default_retry_policy()
        channel = policy.execute(lambda: _create_channel(endpoint))
        return cls(channel, owns_channel=True)

    @classmethod
    def from_channel(cls, channel: grpc.Channel) -> DomainClient:
        """Create a client from a caller-managed channel.

        The returned client will not close the channel when `close()` is called;
        the caller retains ownership.
        """
        return cls(channel, owns_channel=False)

    @classmethod
    def from_clients(
        cls,
        command_handler: CommandHandlerClient,
        query: QueryClient,
    ) -> DomainClient:
        """Compose a DomainClient from already-constructed wrapped clients.

        Bypasses channel construction — the wrapped clients hold their
        own channels (or are duck-typed test doubles). The DomainClient
        does not own the channel; ``close()`` is a no-op for the
        composed instance.

        Used by cucumber/unit tests to inject recording mocks without
        spinning up a gRPC server (PARITY_AUDIT.md plan item P1.12.e /
        finding #24).
        """
        instance = cls.__new__(cls)
        instance.command_handler = command_handler
        instance.query = query
        instance._channel = None
        instance._owns_channel = False
        return instance

    @classmethod
    def for_domain(cls, domain: str, mode: TransportMode | None = None) -> DomainClient:
        """Connect to a domain's command handler coordinator.

        Resolves the domain name to the appropriate endpoint based on transport mode.

        Args:
            domain: Domain name
            mode: Transport mode (standalone=UDS, distributed=K8s DNS).
                  If None, detected from ANGZARR_MODE env var.

        Returns:
            DomainClient connected to the domain's command handler coordinator.

        Examples:
            # Auto-detect mode from ANGZARR_MODE env var
            client = DomainClient.for_domain(domain)

            # Explicitly use standalone mode (Unix Domain Sockets)
            client = DomainClient.for_domain(domain, TransportMode.STANDALONE)

            # Explicitly use distributed mode (K8s DNS)
            client = DomainClient.for_domain(domain, TransportMode.DISTRIBUTED)
        """
        endpoint = resolve_ch_endpoint(domain, mode)
        return cls.connect(endpoint)

    @classmethod
    def from_env(cls, env_var: str, default: str) -> DomainClient:
        """Connect using an environment variable with fallback."""
        endpoint = os.environ.get(env_var) or default
        return cls.connect(endpoint)

    def execute(
        self, command: CommandBook, timeout: float | None = None
    ) -> CommandResponse:
        """Send a command and wait for its decision (see
        :meth:`CommandHandlerClient.handle`)."""
        return self.command_handler.handle(command, timeout=timeout)

    def get_event_book(self, query: Query, timeout: float | None = None) -> EventBook:
        """Retrieve a single EventBook for the query — unary RPC.

        Delegates to the underlying :class:`QueryClient`. Mirrors Rust's
        ``DomainClient::get_event_book``.
        """
        return self.query.get_event_book(query, timeout=timeout)

    def get_events(self, query: Query, timeout: float | None = None) -> list[EventBook]:
        """Retrieve all matching EventBooks for the query — streaming RPC.

        Delegates to the underlying :class:`QueryClient`. Mirrors Rust's
        ``DomainClient::get_events``.
        """
        return self.query.get_events(query, timeout=timeout)

    def get_correlated_events(
        self, correlation_id: PyUUID | str, timeout: float | None = None
    ) -> list[EventBook]:
        """Every event of one correlation, across all domains (see
        :meth:`QueryClient.get_correlated_events`)."""
        return self.query.get_correlated_events(correlation_id, timeout=timeout)

    def close(self) -> None:
        """Close the underlying channel if this client owns it."""
        if self._owns_channel:
            self._channel.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
