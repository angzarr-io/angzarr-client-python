"""Client implementations for Angzarr gRPC services."""

from __future__ import annotations

import os
from enum import Enum
from typing import TYPE_CHECKING
from uuid import UUID as PyUUID

import grpc

from .errors import GRPCError
from .helpers import correlated_metadata
from .retry import RetryPolicy, default_retry_policy

if TYPE_CHECKING:
    from .builder import CommandBuilder, QueryBuilder
from .proto.angzarr import (
    CascadeErrorMode,
    CommandBook,
    CommandHandlerCoordinatorServiceStub,
    CommandRequest,
    CommandResponse,
    EventBook,
    EventQueryServiceStub,
    ProcessManagerCoordinatorServiceStub,
    ProcessManagerHandleResponse,
    Projection,
    ProjectorCoordinatorServiceStub,
    Query,
    SagaCoordinatorServiceStub,
    SagaResponse,
    SpeculateCommandHandlerRequest,
    SpeculatePmRequest,
    SpeculateProjectorRequest,
    SpeculateSagaRequest,
    SyncMode,
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
        domain: The domain name (e.g., "player", "table", "hand")
        mode: Transport mode. If None, detected from ANGZARR_MODE env var.
        uds_base: Base path for Unix Domain Sockets (standalone mode)
        namespace: Kubernetes namespace (distributed mode)
        port: gRPC port (distributed mode)

    Returns:
        Endpoint string suitable for _create_channel:
        - Standalone: /tmp/angzarr/ch-player.sock
        - Distributed: ch-player.angzarr.svc:1310

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
    else:
        return grpc.insecure_channel(endpoint)


class QueryClient:
    """Client for the EventQueryService."""

    def __init__(self, channel: grpc.Channel, owns_channel: bool = True):
        self._stub = EventQueryServiceStub(channel)
        self._channel = channel
        self._owns_channel = owns_channel

    @classmethod
    def connect(cls, endpoint: str, retry: RetryPolicy | None = None) -> "QueryClient":
        """Connect to an event query service at the given endpoint.

        Args:
            endpoint: The gRPC endpoint (host:port or UDS path).
            retry: Optional retry policy. Defaults to exponential backoff.
        """
        policy = retry or default_retry_policy()
        channel = policy.execute(lambda: _create_channel(endpoint))
        return cls(channel, owns_channel=True)

    @classmethod
    def from_channel(cls, channel: grpc.Channel) -> "QueryClient":
        """Create a client from a caller-managed channel.

        The returned client will not close the channel when `close()` is called;
        the caller retains ownership.
        """
        return cls(channel, owns_channel=False)

    @classmethod
    def from_env(cls, env_var: str, default: str) -> "QueryClient":
        """Connect using an environment variable with fallback."""
        endpoint = os.environ.get(env_var, default)
        return cls.connect(endpoint)

    @classmethod
    def from_stub(cls, stub) -> "QueryClient":
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
        ``query.cover.correlation_id`` so OTel exporters / mesh sidecars
        can filter on it without decoding the body. Send-only.
        """
        md = correlated_metadata(query.cover.correlation_id)
        try:
            return self._stub.GetEventBook(query, timeout=timeout, metadata=md)
        except grpc.RpcError as e:
            raise GRPCError(e) from e

    def get_events(self, query: Query, timeout: float | None = None) -> list[EventBook]:
        """Retrieve all EventBooks matching the query.

        Args:
            query: The query specification.
            timeout: Optional per-call deadline in seconds.
        """
        md = correlated_metadata(query.cover.correlation_id)
        try:
            return list(self._stub.GetEvents(query, timeout=timeout, metadata=md))
        except grpc.RpcError as e:
            raise GRPCError(e) from e

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

    def __enter__(self) -> "QueryClient":
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
    ) -> "CommandHandlerClient":
        """Connect to a command handler coordinator at the given endpoint.

        Args:
            endpoint: The gRPC endpoint (host:port or UDS path).
            retry: Optional retry policy. Defaults to exponential backoff.
        """
        policy = retry or default_retry_policy()
        channel = policy.execute(lambda: _create_channel(endpoint))
        return cls(channel, owns_channel=True)

    @classmethod
    def from_channel(cls, channel: grpc.Channel) -> "CommandHandlerClient":
        """Create a client from a caller-managed channel.

        The returned client will not close the channel when `close()` is called;
        the caller retains ownership.
        """
        return cls(channel, owns_channel=False)

    @classmethod
    def from_env(cls, env_var: str, default: str) -> "CommandHandlerClient":
        """Connect using an environment variable with fallback."""
        endpoint = os.environ.get(env_var, default)
        return cls.connect(endpoint)

    @classmethod
    def from_stub(cls, stub) -> "CommandHandlerClient":
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
        """Execute a command with default async mode.

        This is a convenience method that wraps the command in a CommandRequest
        with SYNC_MODE_ASYNC (fire-and-forget) and CASCADE_ERROR_FAIL_FAST.

        Args:
            command: The command to execute.
            timeout: Optional per-call deadline in seconds.
        """
        request = CommandRequest(
            command=command,
            sync_mode=SyncMode.SYNC_MODE_ASYNC,
            cascade_error_mode=CascadeErrorMode.CASCADE_ERROR_FAIL_FAST,
        )
        return self.handle_command(request, timeout=timeout)

    def handle_command(
        self, request: CommandRequest, timeout: float | None = None
    ) -> CommandResponse:
        """Execute a command with the specified sync mode.

        Args:
            request: The command request.
            timeout: Optional per-call deadline in seconds.

        Audit #69 stage (b): attaches ``x-correlation-id`` metadata
        from the canonical ``request.command.cover.correlation_id``.
        """
        md = correlated_metadata(request.command.cover.correlation_id)
        try:
            return self._stub.HandleCommand(request, timeout=timeout, metadata=md)
        except grpc.RpcError as e:
            raise GRPCError(e) from e

    def handle_sync_speculative(
        self,
        request: SpeculateCommandHandlerRequest,
        timeout: float | None = None,
    ) -> CommandResponse:
        """Execute a command speculatively against temporal state (no persistence).

        Args:
            request: The speculative command request.
            timeout: Optional per-call deadline in seconds.
        """
        md = correlated_metadata(request.command.cover.correlation_id)
        try:
            return self._stub.HandleSyncSpeculative(
                request, timeout=timeout, metadata=md
            )
        except grpc.RpcError as e:
            raise GRPCError(e) from e

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

    def __enter__(self) -> "CommandHandlerClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


class SpeculativeClient:
    """Client for speculative operations across coordinator services.

    Speculative execution runs commands/events against temporal state without persistence.
    Each coordinator service now provides its own speculative method.
    """

    def __init__(self, channel: grpc.Channel, owns_channel: bool = True):
        self._command_handler_stub = CommandHandlerCoordinatorServiceStub(channel)
        self._saga_stub = SagaCoordinatorServiceStub(channel)
        self._projector_stub = ProjectorCoordinatorServiceStub(channel)
        self._pm_stub = ProcessManagerCoordinatorServiceStub(channel)
        self._channel = channel
        self._owns_channel = owns_channel

    @classmethod
    def connect(
        cls, endpoint: str, retry: RetryPolicy | None = None
    ) -> "SpeculativeClient":
        """Connect to coordinator services at the given endpoint.

        Args:
            endpoint: The gRPC endpoint (host:port or UDS path).
            retry: Optional retry policy. Defaults to exponential backoff.
        """
        policy = retry or default_retry_policy()
        channel = policy.execute(lambda: _create_channel(endpoint))
        return cls(channel, owns_channel=True)

    @classmethod
    def from_channel(cls, channel: grpc.Channel) -> "SpeculativeClient":
        """Create a client from a caller-managed channel.

        The returned client will not close the channel when `close()` is called;
        the caller retains ownership.
        """
        return cls(channel, owns_channel=False)

    @classmethod
    def from_env(cls, env_var: str, default: str) -> "SpeculativeClient":
        """Connect using an environment variable with fallback."""
        endpoint = os.environ.get(env_var, default)
        return cls.connect(endpoint)

    @classmethod
    def from_stubs(
        cls,
        command_handler_stub,
        saga_stub,
        projector_stub,
        pm_stub,
    ) -> "SpeculativeClient":
        """Compose a SpeculativeClient from pre-built gRPC stubs.

        Bypasses channel construction; the four stubs back the four
        speculative methods (`command_handler`, `saga`, `projector`,
        `process_manager`). Test-only seam — see
        `tests/_fakes.py::RecordingStub`. P1.12.e / finding #24.
        """
        instance = cls.__new__(cls)
        instance._command_handler_stub = command_handler_stub
        instance._saga_stub = saga_stub
        instance._projector_stub = projector_stub
        instance._pm_stub = pm_stub
        instance._channel = None
        instance._owns_channel = False
        return instance

    def command_handler(
        self,
        request: SpeculateCommandHandlerRequest,
        timeout: float | None = None,
    ) -> CommandResponse:
        """Execute a command speculatively against temporal state.

        Audit #69 stage (b): canonical correlation_id at
        ``request.command.cover.correlation_id``.
        """
        md = correlated_metadata(request.command.cover.correlation_id)
        try:
            return self._command_handler_stub.HandleSyncSpeculative(
                request, timeout=timeout, metadata=md
            )
        except grpc.RpcError as e:
            raise GRPCError(e) from e

    def projector(
        self, request: SpeculateProjectorRequest, timeout: float | None = None
    ) -> Projection:
        """Speculatively execute a projector against events.

        Audit #69 stage (b): canonical correlation_id at
        ``request.events.cover.correlation_id``.
        """
        md = correlated_metadata(request.events.cover.correlation_id)
        try:
            return self._projector_stub.HandleSpeculative(
                request, timeout=timeout, metadata=md
            )
        except grpc.RpcError as e:
            raise GRPCError(e) from e

    def saga(
        self, request: SpeculateSagaRequest, timeout: float | None = None
    ) -> SagaResponse:
        """Speculatively execute a saga against events.

        Audit #69 stage (b): canonical correlation_id at
        ``request.request.source.cover.correlation_id``.
        """
        md = correlated_metadata(request.request.source.cover.correlation_id)
        try:
            return self._saga_stub.ExecuteSpeculative(
                request, timeout=timeout, metadata=md
            )
        except grpc.RpcError as e:
            raise GRPCError(e) from e

    def process_manager(
        self, request: SpeculatePmRequest, timeout: float | None = None
    ) -> ProcessManagerHandleResponse:
        """Speculatively execute a process manager.

        Audit #69 stage (b): canonical correlation_id at
        ``request.request.trigger.cover.correlation_id``.
        """
        md = correlated_metadata(request.request.trigger.cover.correlation_id)
        try:
            return self._pm_stub.HandleSpeculative(
                request, timeout=timeout, metadata=md
            )
        except grpc.RpcError as e:
            raise GRPCError(e) from e

    def close(self) -> None:
        """Close the underlying channel if this client owns it."""
        if self._owns_channel:
            self._channel.close()

    def __enter__(self) -> "SpeculativeClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


class DomainClient:
    """Combined client for command handler, query, and speculative operations on a single domain."""

    def __init__(self, channel: grpc.Channel, owns_channel: bool = True):
        self.command_handler = CommandHandlerClient.from_channel(channel)
        self.query = QueryClient.from_channel(channel)
        self.speculative = SpeculativeClient.from_channel(channel)
        self._channel = channel
        self._owns_channel = owns_channel

    @classmethod
    def connect(cls, endpoint: str, retry: RetryPolicy | None = None) -> "DomainClient":
        """Connect to a domain's coordinator at the given endpoint.

        Args:
            endpoint: The gRPC endpoint (host:port or UDS path).
            retry: Optional retry policy. Defaults to exponential backoff.
        """
        policy = retry or default_retry_policy()
        channel = policy.execute(lambda: _create_channel(endpoint))
        return cls(channel, owns_channel=True)

    @classmethod
    def from_channel(cls, channel: grpc.Channel) -> "DomainClient":
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
        speculative: SpeculativeClient,
    ) -> "DomainClient":
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
        instance.speculative = speculative
        instance._channel = None
        instance._owns_channel = False
        return instance

    @classmethod
    def for_domain(
        cls, domain: str, mode: TransportMode | None = None
    ) -> "DomainClient":
        """Connect to a domain's command handler coordinator.

        Resolves the domain name to the appropriate endpoint based on transport mode.

        Args:
            domain: Domain name (e.g., "player", "table")
            mode: Transport mode (standalone=UDS, distributed=K8s DNS).
                  If None, detected from ANGZARR_MODE env var.

        Returns:
            DomainClient connected to the domain's command handler coordinator.

        Examples:
            # Auto-detect mode from ANGZARR_MODE env var
            player = DomainClient.for_domain("player")

            # Explicitly use standalone mode (Unix Domain Sockets)
            player = DomainClient.for_domain("player", TransportMode.STANDALONE)

            # Explicitly use distributed mode (K8s DNS)
            player = DomainClient.for_domain("player", TransportMode.DISTRIBUTED)
        """
        endpoint = resolve_ch_endpoint(domain, mode)
        return cls.connect(endpoint)

    @classmethod
    def from_env(cls, env_var: str, default: str) -> "DomainClient":
        """Connect using an environment variable with fallback."""
        endpoint = os.environ.get(env_var, default)
        return cls.connect(endpoint)

    def execute(
        self, command: CommandBook, timeout: float | None = None
    ) -> CommandResponse:
        """Execute a command with default async mode (fire-and-forget)."""
        return self.execute_with_mode(
            command, SyncMode.SYNC_MODE_ASYNC, timeout=timeout
        )

    def execute_with_mode(
        self,
        command: CommandBook,
        sync_mode: SyncMode,
        timeout: float | None = None,
    ) -> CommandResponse:
        """Execute a command with the specified sync mode.

        Args:
            command: The command to execute.
            sync_mode: Execution mode (ASYNC, SIMPLE, or CASCADE).
            timeout: Optional per-call deadline in seconds.
        """
        request = CommandRequest(
            command=command,
            sync_mode=sync_mode,
            cascade_error_mode=CascadeErrorMode.CASCADE_ERROR_FAIL_FAST,
        )
        return self.command_handler.handle_command(request, timeout=timeout)

    def get_event_book(self, query: Query, timeout: float | None = None) -> EventBook:
        """Retrieve a single EventBook for the query — unary RPC.

        Delegates to the underlying :class:`QueryClient`. Mirrors Rust's
        ``DomainClient::get_event_book``.
        """
        return self.query.get_event_book(query, timeout=timeout)

    def get_events(
        self, query: Query, timeout: float | None = None
    ) -> list[EventBook]:
        """Retrieve all matching EventBooks for the query — streaming RPC.

        Delegates to the underlying :class:`QueryClient`. Mirrors Rust's
        ``DomainClient::get_events``.
        """
        return self.query.get_events(query, timeout=timeout)

    def close(self) -> None:
        """Close the underlying channel if this client owns it."""
        if self._owns_channel:
            self._channel.close()

    def __enter__(self) -> "DomainClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
