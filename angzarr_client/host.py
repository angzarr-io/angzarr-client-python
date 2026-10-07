"""ComponentHost: serves components registered with the router binding over
gRPC, next to the gRPC health service.

The host knows framework concepts only. An application registers its
components (generated ``new_<component>_dispatch(handler)`` tables, or
hand-built ones) and, optionally, its own gRPC services; the host exposes
the coordinator-facing framework services for the kinds registered:

- ``CommandHandlerService`` (aggregates): Handle (commands and the
  rejection / undo notification envelopes), HandleFact, Replay; with a
  routine snapshot interval (``ANGZARR_SNAPSHOT_EVERY``), Handle returns the
  aggregate's state as a snapshot whenever the stream crosses a multiple of
  it;
- ``SagaService`` / ``ProcessManagerService``: Handle;
- ``ProjectorService``: Handle, and HandleSpeculative dispatched with
  ``speculative=True``: projector handlers see ``PageContext.speculative``
  (``current_page().speculative`` in the finisher) and must leave durable and
  external state untouched;
- ``UpcasterService``: Upcast, over an application upcaster.

Dispatch is the router's. A coded failure from the router travels as its
gRPC status: the status message is the error's message, and the
``grpc-status-details-bin`` trailer carries a ``google.rpc.Status`` whose
``ErrorInfo.reason`` is the error code. Saga and process-manager
coordinators deliver every event of their source domains, so an event no
component handles is acknowledged with an empty response.

Health doubles as readiness: every served service (and the overall server,
``""``) reports ``NOT_SERVING`` until the readiness supervisor has seen the
server listening (and the probes in ``readiness`` pass), and again once
shutdown begins; in-flight calls then complete within the grace period. A
Unix-socket host removes its socket file on shutdown.

The host runs its asyncio gRPC server on a private event-loop thread, so it
is used from ordinary synchronous code: ``start()`` / ``stop()``, or
``run()`` to serve until SIGTERM / SIGINT.
"""

from __future__ import annotations

import asyncio
import os
import signal
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import grpc
import structlog
from grpc_health.v1 import health_pb2, health_pb2_grpc
from grpc_health.v1.health import aio as health_aio

from . import readiness
from .error_codes import codes, messages
from .errors import ClientError
from .proto.io.angzarr.v1 import (
    command_handler_pb2,
    command_handler_pb2_grpc,
    process_manager_pb2,
    process_manager_pb2_grpc,
    projector_pb2,
    projector_pb2_grpc,
    saga_pb2,
    saga_pb2_grpc,
    upcaster_pb2,
    upcaster_pb2_grpc,
)
from .readiness import (
    BusProbe,
    OutputDomainProbe,
    Probe,
    TransportProbe,
    TransportSignal,
)
from .server import get_transport_config

if TYPE_CHECKING:
    from typing_extensions import Self

_SERVING = health_pb2.HealthCheckResponse.SERVING
_NOT_SERVING = health_pb2.HealthCheckResponse.NOT_SERVING
_STATUS_DETAILS_KEY = "grpc-status-details-bin"
_STATUS_BY_CODE = {status.value[0]: status for status in grpc.StatusCode}

COMMAND_HANDLER_SERVICE = command_handler_pb2.DESCRIPTOR.services_by_name[
    "CommandHandlerService"
].full_name
SAGA_SERVICE = saga_pb2.DESCRIPTOR.services_by_name["SagaService"].full_name
PROCESS_MANAGER_SERVICE = process_manager_pb2.DESCRIPTOR.services_by_name[
    "ProcessManagerService"
].full_name
PROJECTOR_SERVICE = projector_pb2.DESCRIPTOR.services_by_name[
    "ProjectorService"
].full_name
UPCASTER_SERVICE = upcaster_pb2.DESCRIPTOR.services_by_name["UpcasterService"].full_name
#: The routine snapshot interval a deployment gives an aggregate component.
SNAPSHOT_EVERY_ENV = "ANGZARR_SNAPSHOT_EVERY"

#: The framework services a host can serve; anything else it serves is an
#: application service registered with :meth:`ComponentHost.add_service`.
FRAMEWORK_SERVICES = (
    COMMAND_HANDLER_SERVICE,
    SAGA_SERVICE,
    PROCESS_MANAGER_SERVICE,
    PROJECTOR_SERVICE,
    UPCASTER_SERVICE,
)


class ConfigurationError(ClientError):
    """The host cannot start as configured."""


class Upcaster(Protocol):
    """An application upcaster: legacy event shapes in, current shapes out."""

    def upcast(
        self, request: upcaster_pb2.UpcastRequest
    ) -> upcaster_pb2.UpcastResponse: ...


class PassThroughUpcaster:
    """The upcaster of a domain with no legacy event shapes: events come back
    unchanged."""

    def upcast(
        self, request: upcaster_pb2.UpcastRequest
    ) -> upcaster_pb2.UpcastResponse:
        return upcaster_pb2.UpcastResponse(events=request.events)


def grpc_status_code(code: int) -> grpc.StatusCode:
    """The ``grpc.StatusCode`` for a numeric gRPC code (INTERNAL when unknown)."""
    return _STATUS_BY_CODE.get(int(code), grpc.StatusCode.INTERNAL)


@dataclass
class _Service:
    name: str
    add_to_server: Callable
    servicer: object


class _Dispatcher:
    """Runs blocking router dispatch on the host's worker pool and reports
    failures as gRPC statuses."""

    def __init__(self, executor: ThreadPoolExecutor, logger) -> None:
        self._executor = executor
        self._log = logger

    async def call(self, context, fn: Callable, request, *, ack_unconsumed=None):
        from .router import CodedError, GrpcCode
        from .router._dispatch import _build_status_bytes

        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(self._executor, fn, request)
        except CodedError as exc:
            if ack_unconsumed is not None and exc.grpc == GrpcCode.UNIMPLEMENTED:
                return ack_unconsumed()
            details = _build_status_bytes(exc.grpc, exc.message, exc.code, exc.extras)
            context.set_trailing_metadata(((_STATUS_DETAILS_KEY, details),))
            await context.abort(grpc_status_code(exc.grpc), exc.message)
        # The transport edge reports every other failure as a coded INTERNAL.
        except Exception as exc:  # noqa: BLE001
            self._log.error("dispatch_failed", error=str(exc))
            details = _build_status_bytes(
                int(grpc.StatusCode.INTERNAL.value[0]),
                messages.HANDLER_PANICKED,
                codes.HANDLER_PANICKED,
                {"error": str(exc)},
            )
            context.set_trailing_metadata(((_STATUS_DETAILS_KEY, details),))
            await context.abort(grpc.StatusCode.INTERNAL, messages.HANDLER_PANICKED)


def snapshot_interval(explicit: int | None = None) -> int | None:
    """The routine snapshot interval: ``explicit``, else ``ANGZARR_SNAPSHOT_EVERY``,
    else none. An interval must be a positive number of events."""
    if explicit is not None:
        if isinstance(explicit, bool) or not isinstance(explicit, int) or explicit < 1:
            raise ConfigurationError(
                f"snapshot_every must be a positive number of events, got {explicit!r}"
            )
        return explicit
    raw = os.environ.get(SNAPSHOT_EVERY_ENV, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if value < 1:
        raise ConfigurationError(
            f"{SNAPSHOT_EVERY_ENV} must be a positive number of events, got {raw!r}"
        )
    return value


def _stream_length(prior) -> int:
    """How many events the aggregate's stream holds before the command."""
    if prior.next_sequence:
        return prior.next_sequence
    if prior.pages:
        return prior.pages[-1].header.sequence + 1
    if prior.HasField("snapshot"):
        return prior.snapshot.sequence + 1
    return 0


def _attach_routine_snapshot(router, domain: str, prior, response, every: int) -> None:
    """Attach the aggregate's state after the new events as a routine
    (RETENTION_DEFAULT) snapshot when they carry the stream across a multiple
    of ``every``. A snapshot the handler returned itself is kept as is."""
    if response.WhichOneof("result") != "events":
        return
    book = response.events
    if not book.pages or book.HasField("snapshot"):
        return
    before = _stream_length(prior)
    if (before + len(book.pages)) // every == before // every:
        return
    replay = command_handler_pb2.ReplayRequest()
    if prior.HasField("snapshot"):
        replay.base_snapshot.CopyFrom(prior.snapshot)
    replay.events.extend(prior.pages)
    for offset, page in enumerate(book.pages):
        added = replay.events.add()
        added.CopyFrom(page)
        added.header.sequence = before + offset
    book.snapshot.state.CopyFrom(router.dispatch_replay(domain, replay).state)


class _CommandHandlerServicer(command_handler_pb2_grpc.CommandHandlerServiceServicer):
    def __init__(
        self,
        router,
        domains: list[str],
        dispatcher: _Dispatcher,
        snapshot_every: int | None = None,
    ) -> None:
        self._router = router
        self._domains = domains
        self._dispatcher = dispatcher
        self._snapshot_every = snapshot_every

    def _handle(self, request):
        response = self._router.dispatch(request)
        if self._snapshot_every is not None and len(self._domains) == 1:
            _attach_routine_snapshot(
                self._router,
                self._domains[0],
                request.events,
                response,
                self._snapshot_every,
            )
        return response

    async def Handle(self, request, context):
        return await self._dispatcher.call(context, self._handle, request)

    async def HandleFact(self, request, context):
        return await self._dispatcher.call(context, self._router.dispatch_fact, request)

    async def Replay(self, request, context):
        # ReplayRequest names no domain: a host replays for its one
        # aggregate. With several, the request is ambiguous and the host
        # reports replay as unsupported (the coordinator falls back to
        # strict merging).
        if len(self._domains) != 1:
            from .router import GrpcCode
            from .router._dispatch import _build_status_bytes

            details = _build_status_bytes(
                GrpcCode.UNIMPLEMENTED,
                messages.HANDLER_DOES_NOT_SUPPORT_REPLAY,
                codes.HANDLER_DOES_NOT_SUPPORT_REPLAY,
                {"domains": ",".join(self._domains)},
            )
            context.set_trailing_metadata(((_STATUS_DETAILS_KEY, details),))
            await context.abort(
                grpc.StatusCode.UNIMPLEMENTED, messages.HANDLER_DOES_NOT_SUPPORT_REPLAY
            )
        domain = self._domains[0]
        return await self._dispatcher.call(
            context, lambda req: self._router.dispatch_replay(domain, req), request
        )


class _SagaServicer(saga_pb2_grpc.SagaServiceServicer):
    def __init__(self, router, dispatcher: _Dispatcher) -> None:
        self._router = router
        self._dispatcher = dispatcher

    async def Handle(self, request, context):
        return await self._dispatcher.call(
            context,
            self._router.dispatch_saga,
            request,
            ack_unconsumed=saga_pb2.SagaResponse,
        )


class _ProcessManagerServicer(process_manager_pb2_grpc.ProcessManagerServiceServicer):
    def __init__(self, router, dispatcher: _Dispatcher) -> None:
        self._router = router
        self._dispatcher = dispatcher

    async def Handle(self, request, context):
        return await self._dispatcher.call(
            context,
            self._router.dispatch_process_manager,
            request,
            ack_unconsumed=process_manager_pb2.ProcessManagerHandleResponse,
        )


class _ProjectorServicer(projector_pb2_grpc.ProjectorServiceServicer):
    def __init__(self, router, dispatcher: _Dispatcher) -> None:
        self._router = router
        self._dispatcher = dispatcher

    async def Handle(self, request, context):
        return await self._dispatcher.call(
            context, self._router.dispatch_projector, request
        )

    async def HandleSpeculative(self, request, context):
        return await self._dispatcher.call(context, self._speculate, request)

    def _speculate(self, request):
        return self._router.dispatch_projector(request, speculative=True)


class _UpcasterServicer(upcaster_pb2_grpc.UpcasterServiceServicer):
    def __init__(self, upcaster: Upcaster, dispatcher: _Dispatcher) -> None:
        self._upcaster = upcaster
        self._dispatcher = dispatcher

    async def Upcast(self, request, context):
        return await self._dispatcher.call(context, self._upcaster.upcast, request)


class _RecordingHealth(health_aio.HealthServicer):
    """The aio health servicer, remembering every status published per
    service name in order."""

    def __init__(self) -> None:
        super().__init__()
        self.history: dict[str, list[int]] = {}

    async def set(self, service, status):
        self.history.setdefault(service, []).append(status)
        await super().set(service, status)


class ComponentHost:
    """Serves the components registered on a router over gRPC.

    ``router`` defaults to a new :class:`angzarr_client.router.Router`, which
    the host then closes on :meth:`stop`. ``max_workers`` bounds concurrent
    dispatches (router dispatch blocks on the native call and on handler
    code). ``sync_output_domains`` names command targets whose coordinators
    must be reachable before the host reports SERVING
    (:class:`readiness.OutputDomainProbe`); when sagas or process managers
    are registered and ``ANGZARR_BUS_ENDPOINT`` is set, the bus must be
    reachable too. ``snapshot_every`` (default ``ANGZARR_SNAPSHOT_EVERY``)
    asks for a routine snapshot of a single hosted aggregate whenever a
    command's events carry its stream across a multiple of that many events.
    """

    def __init__(
        self,
        router=None,
        *,
        max_workers: int = 16,
        sync_output_domains: list[str] | None = None,
        snapshot_every: int | None = None,
        logger=None,
    ) -> None:
        self._snapshot_every = snapshot_interval(snapshot_every)
        if router is None:
            from .router import Router

            router = Router()
            self._owns_router = True
        else:
            self._owns_router = False
        self.router = router
        self._max_workers = max_workers
        self._sync_output_domains = list(sync_output_domains or [])
        self._log = logger if logger is not None else structlog.get_logger(__name__)
        self._aggregate_domains: list[str] = []
        self._kinds: set[str] = set()
        self._upcaster: Upcaster | None = None
        self._app_services: list[_Service] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._server: grpc.aio.Server | None = None
        self._health: _RecordingHealth | None = None
        self._supervisor: asyncio.Task | None = None
        self._executor: ThreadPoolExecutor | None = None
        self._socket_path: str | None = None
        self._stopped = threading.Event()
        self.address = ""

    # --- registration --------------------------------------------------

    def add_aggregate(self, dispatch) -> ComponentHost:
        """Register an aggregate's dispatch table; serves CommandHandlerService."""
        self.router.register_aggregate(dispatch)
        self._aggregate_domains.append(dispatch.domain)
        self._kinds.add(COMMAND_HANDLER_SERVICE)
        return self

    def add_saga(self, dispatch) -> ComponentHost:
        """Register a saga's dispatch table; serves SagaService."""
        self.router.register_saga(dispatch)
        self._kinds.add(SAGA_SERVICE)
        return self

    def add_process_manager(self, dispatch) -> ComponentHost:
        """Register a process manager's dispatch table; serves ProcessManagerService."""
        self.router.register_process_manager(dispatch)
        self._kinds.add(PROCESS_MANAGER_SERVICE)
        return self

    def add_projector(self, dispatch) -> ComponentHost:
        """Register a projector's dispatch table; serves ProjectorService."""
        self.router.register_projector(dispatch)
        self._kinds.add(PROJECTOR_SERVICE)
        return self

    def add_upcaster(self, upcaster: Upcaster) -> ComponentHost:
        """Serve UpcasterService over ``upcaster``."""
        self._upcaster = upcaster
        self._kinds.add(UPCASTER_SERVICE)
        return self

    def add_service(
        self, add_to_server: Callable, servicer: object, name: str
    ) -> ComponentHost:
        """Serve an application gRPC service: ``add_to_server(servicer,
        server)`` registers it (a generated ``add_<Service>Servicer_to_server``,
        or any callable taking the servicer and the server); ``name`` is its
        fully-qualified service name, reported by the health service."""
        if not name:
            raise ConfigurationError(
                "an application service needs its full service name"
            )
        self._app_services.append(_Service(name, add_to_server, servicer))
        return self

    @property
    def services(self) -> list[str]:
        """The full names of every service the host serves (health excepted)."""
        framework = [name for name in FRAMEWORK_SERVICES if name in self._kinds]
        return framework + [s.name for s in self._app_services]

    # --- lifecycle -----------------------------------------------------

    def start(self, address: str | None = None) -> str:
        """Start serving and return the bound address. ``address`` defaults
        to the transport the environment selects
        (:func:`angzarr_client.server.get_transport_config`); a TCP port of 0
        binds a free port, reflected in the returned address."""
        if not self._kinds - {UPCASTER_SERVICE}:
            raise ConfigurationError(
                f"{codes.NO_COMPONENTS_REGISTERED}: {messages.NO_COMPONENTS_REGISTERED}"
            )
        if self._thread is not None:
            raise ConfigurationError("the host is already started")
        if address is None:
            _transport, address = get_transport_config()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, name="angzarr-component-host", daemon=True
        )
        self._thread.start()
        try:
            self.address = asyncio.run_coroutine_threadsafe(
                self._start(address), self._loop
            ).result()
        except BaseException:
            self._shutdown_loop()
            raise
        return self.address

    def stop(self, grace: float | None = 5.0) -> None:
        """Stop serving: every health status turns NOT_SERVING, new calls are
        refused, in-flight calls complete within ``grace`` seconds, a Unix
        socket file is removed and an owned router is closed. Idempotent."""
        if self._stopped.is_set():
            return
        if self._loop is None:
            if self._owns_router:
                self.router.close()
            self._stopped.set()
            return
        try:
            asyncio.run_coroutine_threadsafe(self._stop(grace), self._loop).result()
        finally:
            self._shutdown_loop()
            if self._owns_router:
                self.router.close()
            self._stopped.set()

    def wait(self, timeout: float | None = None) -> bool:
        """Block until the host stops; True once it has."""
        return self._stopped.wait(timeout)

    def run(self, address: str | None = None, grace: float = 5.0) -> None:
        """Start, serve until SIGTERM or SIGINT, then stop. The handlers are
        installed before the server starts, so a signal during startup stops
        the host gracefully; the previous handlers are restored on return."""
        stopping = threading.Event()

        def _request_stop(signum, frame):
            stopping.set()

        previous = {
            sig: signal.signal(sig, _request_stop)
            for sig in (signal.SIGTERM, signal.SIGINT)
        }
        try:
            self.start(address)
            self._log.info(
                "server_started", services=self.services, address=self.address
            )
            # Python runs signal handlers on the main thread, but the kernel
            # may deliver the signal to another thread; a timed wait returns
            # to the interpreter regularly so the handler still runs.
            while not stopping.wait(0.2):
                pass
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
            started = bool(self.address)
            self.stop(grace)
            if started:
                self._log.info("server_shutdown", services=self.services)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    # --- health --------------------------------------------------------

    def health_status(self, service: str = "") -> int | None:
        """The health status currently published for ``service`` (``""`` is
        the overall server); None before the host has started."""
        history = self.health_history(service)
        return history[-1] if history else None

    def health_history(self, service: str = "") -> list[int]:
        """Every health status published for ``service``, in order."""
        if self._health is None:
            return []
        return list(self._health.history.get(service, []))

    # --- event-loop side -----------------------------------------------

    async def _start(self, address: str) -> str:
        self._executor = ThreadPoolExecutor(
            max_workers=self._max_workers, thread_name_prefix="angzarr-dispatch"
        )
        dispatcher = _Dispatcher(self._executor, self._log)
        server = grpc.aio.server()
        router = self.router
        if COMMAND_HANDLER_SERVICE in self._kinds:
            command_handler_pb2_grpc.add_CommandHandlerServiceServicer_to_server(
                _CommandHandlerServicer(
                    router,
                    list(self._aggregate_domains),
                    dispatcher,
                    self._snapshot_every,
                ),
                server,
            )
        if SAGA_SERVICE in self._kinds:
            saga_pb2_grpc.add_SagaServiceServicer_to_server(
                _SagaServicer(router, dispatcher), server
            )
        if PROCESS_MANAGER_SERVICE in self._kinds:
            process_manager_pb2_grpc.add_ProcessManagerServiceServicer_to_server(
                _ProcessManagerServicer(router, dispatcher), server
            )
        if PROJECTOR_SERVICE in self._kinds:
            projector_pb2_grpc.add_ProjectorServiceServicer_to_server(
                _ProjectorServicer(router, dispatcher), server
            )
        if UPCASTER_SERVICE in self._kinds:
            upcaster_pb2_grpc.add_UpcasterServiceServicer_to_server(
                _UpcasterServicer(self._upcaster, dispatcher), server
            )
        for service in self._app_services:
            service.add_to_server(service.servicer, server)

        health = _RecordingHealth()
        health_pb2_grpc.add_HealthServicer_to_server(health, server)
        names = ["", *self.services]
        for name in names:
            await health.set(name, _NOT_SERVING)
        self._health = health

        port = server.add_insecure_port(address)
        if address.startswith("unix:"):
            self._socket_path = address[len("unix:") :].removeprefix("//")
            bound = address
        else:
            bound = f"{address.rpartition(':')[0]}:{port}"
        await server.start()
        self._server = server

        signal_bound = TransportSignal()
        signal_bound.mark_bound()
        probes: list[Probe] = [TransportProbe(signal_bound)]
        probes += [OutputDomainProbe.for_domain(d) for d in self._sync_output_domains]
        if self._kinds & {SAGA_SERVICE, PROCESS_MANAGER_SERVICE}:
            bus = BusProbe.from_env()
            if bus is not None:
                probes.append(bus)
        interval, timeout = readiness.probe_config_from_env()
        self._supervisor = asyncio.create_task(
            readiness.run_supervisor(
                probes=probes,
                health_servicer=health,
                service_names=names,
                interval=interval,
                timeout=timeout,
            )
        )
        return bound

    async def _stop(self, grace: float | None) -> None:
        if self._supervisor is not None:
            self._supervisor.cancel()
            try:
                await self._supervisor
            except asyncio.CancelledError:
                pass
        if self._health is not None:
            for name in ["", *self.services]:
                await self._health.set(name, _NOT_SERVING)
        if self._server is not None:
            await self._server.stop(grace)
        if self._executor is not None:
            self._executor.shutdown(wait=True)
        if self._socket_path and os.path.exists(self._socket_path):
            os.remove(self._socket_path)

    def _shutdown_loop(self) -> None:
        loop, thread = self._loop, self._thread
        if loop is None:
            return
        loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=30)
        loop.close()
        self._loop = None
        self._thread = None
