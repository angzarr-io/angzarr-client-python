"""ComponentHost: serves the one component registered with the router
binding over gRPC, next to the gRPC health service.

One business process hosts exactly one component (an aggregate, process
manager, policy or projector) for its one coordinator; a second registration
is not hosted, and the host refuses to start, naming both
(``ROUTER_REASON_DUPLICATE_COMPONENT``). The host knows framework concepts
only. An application registers its component (a generated
``new_<component>_dispatch(handler)`` table, or a hand-built one) and,
optionally, its own gRPC services; the host exposes:

- ``ComponentService``: Describe, answered with the ComponentDescriptor
  angzarr-cli generated for the component, given with it, unchanged
  (decision 14.42). The coordinator pulls it and validates it; the host
  never calls its coordinator. The component carries the binary's build version, given as
  ``ComponentHost(rules_version=...)``, and its descriptor must carry the
  same: the host refuses one of another version when it is registered
  (``HOST_REASON_DESCRIPTOR_RULES_VERSION_MISMATCH``);
- the component's kind service: ``CommandHandlerService`` (an aggregate or
  a process manager, a domain: Handle, for commands and facts alike),
  ``PolicyService`` (Handle and OnRefused) or ``ProjectorService``
  (Handle);

An upcaster is its component's sidecar, a process of its own (decision
14.39): a host given only an upcaster serves ``UpcasterService`` and health,
and nothing else; a host given a component and an upcaster refuses to start
(``HOST_REASON_UPCASTER_WITH_COMPONENT``).

Dispatch is the router's. A coded failure from the router travels as its
gRPC status, a key and an argument map, never text (decision 14.37): the
status message is the error's code, and the ``grpc-status-details-bin``
trailer carries a ``google.rpc.Status`` whose ``ErrorInfo`` has the code as
its reason and the args as its metadata. Any other exception is
``ROUTER_REASON_UNHANDLED_HANDLER_ERROR`` with cause
``ROUTER_REASON_HANDLER_PANICKED``; its text goes to the host's log only.

Health doubles as readiness: every served service (and the overall server,
``""``) reports ``NOT_SERVING`` until the readiness supervisor has seen the
server listening, and again once
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
from .error_codes import codes, keys
from .errors import ClientError
from .proto.io.angzarr.v1 import (
    command_handler_pb2,
    command_handler_pb2_grpc,
    component_pb2,
    component_pb2_grpc,
    policy_pb2,
    policy_pb2_grpc,
    projector_pb2,
    projector_pb2_grpc,
    upcaster_pb2,
    upcaster_pb2_grpc,
)
from .readiness import Probe, TransportProbe, TransportSignal
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
COMPONENT_SERVICE = component_pb2.DESCRIPTOR.services_by_name[
    "ComponentService"
].full_name
POLICY_SERVICE = policy_pb2.DESCRIPTOR.services_by_name["PolicyService"].full_name
PROJECTOR_SERVICE = projector_pb2.DESCRIPTOR.services_by_name[
    "ProjectorService"
].full_name
UPCASTER_SERVICE = upcaster_pb2.DESCRIPTOR.services_by_name["UpcasterService"].full_name

#: The framework services a host can serve; anything else it serves is an
#: application service registered with :meth:`ComponentHost.add_service`.
FRAMEWORK_SERVICES = (
    COMPONENT_SERVICE,
    COMMAND_HANDLER_SERVICE,
    POLICY_SERVICE,
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

    async def call(self, context, fn: Callable, request):
        from .router import CodedError, GrpcCode

        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(self._executor, fn, request)
        except CodedError as exc:
            failure = exc
        # The transport edge reports every other failure as a coded INTERNAL;
        # what escaped stays in the host's log.
        except Exception as exc:  # noqa: BLE001
            self._log.error("dispatch_failed", error=str(exc))
            failure = CodedError(
                codes.ROUTER_REASON_UNHANDLED_HANDLER_ERROR,
                GrpcCode.INTERNAL,
                {keys.CAUSE: codes.ROUTER_REASON_HANDLER_PANICKED},
            )
        details = failure.status().SerializeToString()
        context.set_trailing_metadata(((_STATUS_DETAILS_KEY, details),))
        await context.abort(grpc_status_code(failure.grpc), failure.code)


class _CommandHandlerServicer(command_handler_pb2_grpc.CommandHandlerServiceServicer):
    def __init__(self, router, dispatcher: _Dispatcher) -> None:
        self._router = router
        self._dispatcher = dispatcher

    async def Handle(self, request, context):
        return await self._dispatcher.call(context, self._router.dispatch, request)


class _ComponentServicer(component_pb2_grpc.ComponentServiceServicer):
    def __init__(self, descriptor) -> None:
        self._descriptor = descriptor

    async def Describe(self, request, context):
        return self._descriptor


class _PolicyServicer(policy_pb2_grpc.PolicyServiceServicer):
    def __init__(self, router, dispatcher: _Dispatcher) -> None:
        self._router = router
        self._dispatcher = dispatcher

    async def Handle(self, request, context):
        return await self._dispatcher.call(
            context, self._router.dispatch_policy, request
        )

    async def OnRefused(self, request, context):
        return await self._dispatcher.call(
            context, self._router.dispatch_refusal, request
        )


class _ProjectorServicer(projector_pb2_grpc.ProjectorServiceServicer):
    def __init__(self, router, dispatcher: _Dispatcher) -> None:
        self._router = router
        self._dispatcher = dispatcher

    async def Handle(self, request, context):
        return await self._dispatcher.call(
            context, self._router.dispatch_projector, request
        )


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
    """Serves the one component registered on a router over gRPC.

    ``router`` defaults to a new :class:`angzarr_client.router.Router`, which
    the host then closes on :meth:`stop`. ``max_workers`` bounds concurrent
    dispatches (router dispatch blocks on handler code). ``rules_version`` is the business binary's full build version
    (``__version__`` from the module ``versionator output emit python``
    writes): it is stamped on the hosted table unless it declares its own,
    so every answer and the component's description carry it. The
    coordinator judges the description (decision 14.42).
    """

    def __init__(
        self,
        router=None,
        *,
        rules_version: str = "",
        max_workers: int = 16,
        logger=None,
    ) -> None:
        if router is None:
            from .router import Router

            router = Router()
            self._owns_router = True
        else:
            self._owns_router = False
        self.router = router
        self.rules_version = rules_version
        self._max_workers = max_workers
        self._log = logger if logger is not None else structlog.get_logger(__name__)
        # The one component's kind service, once registered.
        self._kind: str | None = None
        #: The hosted component's generated ComponentDescriptor, which
        #: Describe answers unchanged; None before one is registered.
        self.descriptor = None
        self._component_name: str | None = None
        # Components registered after the first: never hosted; start()
        # refuses while any is.
        self._refused: list[str] = []
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

    def add_aggregate(self, dispatch, descriptor) -> ComponentHost:
        """Register an aggregate's dispatch table and its descriptor; serves
        CommandHandlerService."""
        return self._add(dispatch, descriptor, "aggregate", COMMAND_HANDLER_SERVICE)

    def add_policy(self, dispatch, descriptor) -> ComponentHost:
        """Register a policy's dispatch table and its descriptor; serves
        PolicyService."""
        return self._add(dispatch, descriptor, "policy", POLICY_SERVICE)

    def add_process_manager(self, dispatch, descriptor) -> ComponentHost:
        """Register a process manager's dispatch table and its descriptor:
        a domain, so it serves CommandHandlerService (decision 14.31)."""
        return self._add(
            dispatch, descriptor, "process_manager", COMMAND_HANDLER_SERVICE
        )

    def add_projector(self, dispatch, descriptor) -> ComponentHost:
        """Register a projector's dispatch table and its descriptor; serves
        ProjectorService."""
        return self._add(dispatch, descriptor, "projector", PROJECTOR_SERVICE)

    def _add(self, dispatch, descriptor, kind: str, service: str) -> ComponentHost:
        """Register the host's one component: ``descriptor`` is the
        ComponentDescriptor angzarr-cli generated for it, which Describe
        answers unchanged (decision 14.42). Its ``rules_version`` must be
        the version the component's answers report (the table's own, else
        the host's ``rules_version``): the coordinator refuses an answer
        reporting any other, so a mismatch is refused here, at once, with
        :class:`ConfigurationError` code
        ``HOST_REASON_DESCRIPTOR_RULES_VERSION_MISMATCH`` (details: component,
        described, reported), and nothing is registered."""
        reported = getattr(dispatch, "rules_version", "") or self.rules_version
        if descriptor.rules_version != reported:
            raise ConfigurationError(
                "the descriptor's rules_version is not the version the "
                "component reports",
                code=codes.HOST_REASON_DESCRIPTOR_RULES_VERSION_MISMATCH,
                details={
                    "component": getattr(dispatch, "name", ""),
                    "described": descriptor.rules_version,
                    "reported": reported,
                },
            )
        if self._claim(dispatch):
            getattr(self.router, f"register_{kind}")(self._versioned(dispatch))
            self._kind = service
            self.descriptor = descriptor
        return self

    def _claim(self, dispatch) -> bool:
        """Whether ``dispatch`` is the host's one component. A second is not
        hosted: it is remembered, and :meth:`start` refuses, naming both
        (a host serves exactly one component)."""
        name = getattr(dispatch, "name", "")
        if self._component_name is None:
            self._component_name = name
            return True
        self._refused.append(name)
        return False

    def _versioned(self, dispatch):
        """``dispatch`` with the host's build version, unless it declares
        its own."""
        if self.rules_version and not getattr(dispatch, "rules_version", ""):
            dispatch.rules_version = self.rules_version
        return dispatch

    def add_upcaster(self, upcaster: Upcaster) -> ComponentHost:
        """Serve UpcasterService over ``upcaster``: the host is its
        component's upcaster sidecar (decision 14.39), serving the upcaster
        and health only. Its component's coordinator calls it; a host given
        a component too refuses to start
        (``HOST_REASON_UPCASTER_WITH_COMPONENT``)."""
        self._upcaster = upcaster
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
        served = set()
        if self._kind is not None:
            served |= {COMPONENT_SERVICE, self._kind}
        if self._upcaster is not None:
            served.add(UPCASTER_SERVICE)
        framework = [name for name in FRAMEWORK_SERVICES if name in served]
        return framework + [s.name for s in self._app_services]

    # --- lifecycle -----------------------------------------------------

    def start(self, address: str | None = None) -> str:
        """Start serving and return the bound address. ``address`` defaults
        to the transport the environment selects
        (:func:`angzarr_client.server.get_transport_config`); a TCP port of 0
        binds a free port, reflected in the returned address."""
        if self._kind is not None and self._upcaster is not None:
            raise ConfigurationError(
                codes.HOST_REASON_UPCASTER_WITH_COMPONENT,
                code=codes.HOST_REASON_UPCASTER_WITH_COMPONENT,
                details={"component": self._component_name},
            )
        if self._kind is None and self._upcaster is None:
            raise ConfigurationError(
                codes.HOST_REASON_NO_COMPONENTS_REGISTERED,
                code=codes.HOST_REASON_NO_COMPONENTS_REGISTERED,
            )
        if self._refused:
            raise ConfigurationError(
                codes.ROUTER_REASON_DUPLICATE_COMPONENT,
                code=codes.ROUTER_REASON_DUPLICATE_COMPONENT,
                details={
                    "registered": self._component_name,
                    "refused": ", ".join(self._refused),
                },
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
        if self._kind is not None:
            component_pb2_grpc.add_ComponentServiceServicer_to_server(
                _ComponentServicer(self.descriptor), server
            )
        if self._kind == COMMAND_HANDLER_SERVICE:
            command_handler_pb2_grpc.add_CommandHandlerServiceServicer_to_server(
                _CommandHandlerServicer(router, dispatcher),
                server,
            )
        if self._kind == POLICY_SERVICE:
            policy_pb2_grpc.add_PolicyServiceServicer_to_server(
                _PolicyServicer(router, dispatcher), server
            )
        if self._kind == PROJECTOR_SERVICE:
            projector_pb2_grpc.add_ProjectorServiceServicer_to_server(
                _ProjectorServicer(router, dispatcher), server
            )
        if self._upcaster is not None:
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
