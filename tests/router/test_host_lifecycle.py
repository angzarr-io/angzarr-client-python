"""ComponentHost lifecycle, worker pool, logging and readiness wiring.

A scripted router double stands in for the router binding where a test needs
to control dispatch (block it, fail it with a chosen coded error, or see the
arguments the host passes); the host's contract over the router is the
duck-typed ``register_*`` / ``dispatch*`` surface.
"""

from __future__ import annotations

import os
import re
import signal
import socket
import threading
import time
from types import SimpleNamespace

import grpc
import pytest
import structlog
from grpc_health.v1 import health_pb2

from angzarr_client import ComponentHost, ConfigurationError
from angzarr_client.host import COMMAND_HANDLER_SERVICE, SAGA_SERVICE
from angzarr_client.proto.io.angzarr.v1 import (
    command_handler_pb2,
    command_handler_pb2_grpc,
    process_manager_pb2,
    process_manager_pb2_grpc,
    projector_pb2_grpc,
    saga_pb2,
    saga_pb2_grpc,
    types_pb2,
)
from angzarr_client.router import CodedError, GrpcCode, Router

from . import builders
from .fixture import OrderSaga
from .gen.test.counter import order_saga_angzarr
from .test_host import _counter_host, _error_info, _increased_book, _wait_serving

_SERVING = health_pb2.HealthCheckResponse.SERVING
_NOT_SERVING = health_pb2.HealthCheckResponse.NOT_SERVING
_LOOP_THREAD = "angzarr-component-host"

_RESPONSES = {
    "dispatch": command_handler_pb2.BusinessResponse,
    "dispatch_fact": types_pb2.EventBook,
    "dispatch_replay": command_handler_pb2.ReplayResponse,
    "dispatch_saga": saga_pb2.SagaResponse,
    "dispatch_process_manager": process_manager_pb2.ProcessManagerHandleResponse,
    "dispatch_projector": types_pb2.Projection,
    "dispatch_projector_speculative": types_pb2.Projection,
}


class _ScriptedRouter:
    """A router double: records every dispatch the host makes and answers it
    with ``answer(kind, *args)``."""

    def __init__(self, answer) -> None:
        self.answer = answer
        self.calls: list[tuple[str, tuple]] = []

    def _run(self, kind: str, *args):
        self.calls.append((kind, args))
        return self.answer(kind, *args)

    def register_aggregate(self, dispatch) -> None:
        pass

    def register_saga(self, dispatch) -> None:
        pass

    def register_process_manager(self, dispatch) -> None:
        pass

    def register_projector(self, dispatch) -> None:
        pass

    def close(self) -> None:
        pass

    def dispatch(self, request):
        return self._run("dispatch", request)

    def dispatch_fact(self, request):
        return self._run("dispatch_fact", request)

    def dispatch_replay(self, domain, request):
        return self._run("dispatch_replay", domain, request)

    def dispatch_saga(self, request):
        return self._run("dispatch_saga", request)

    def dispatch_process_manager(self, request):
        return self._run("dispatch_process_manager", request)

    def dispatch_projector(self, request, *, speculative: bool = False):
        if speculative:
            return self._run("dispatch_projector_speculative", request)
        return self._run("dispatch_projector", request)


def _empty_response(kind, *args):
    return _RESPONSES[kind]()


class _Gate:
    """A dispatch answer that blocks until released, tracking how many
    dispatches run at once. ``release`` lets every waiter go; ``releases``
    holds per-domain events for calls told apart by command cover domain."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.active = 0
        self.peak = 0
        self.entered = threading.Event()
        self.release = threading.Event()
        self.releases: dict[str, threading.Event] = {}
        self.finished_at: list[float] = []

    def __call__(self, kind, *args):
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        self.entered.set()
        domain = args[-1].command.cover.domain if kind == "dispatch" else ""
        (self.releases.get(domain) or self.release).wait(20)
        with self.lock:
            self.active -= 1
        self.finished_at.append(time.monotonic())
        return _RESPONSES[kind]()


class _RecordingLogger:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, dict]] = []

    def info(self, event, **kw):
        self.records.append(("info", event, kw))

    def error(self, event, **kw):
        self.records.append(("error", event, kw))


def _within(fn, timeout: float = 10.0):
    """Run ``fn`` on a helper thread; fail rather than hang if it does not
    return within ``timeout``."""
    box: dict = {}

    def target():
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 — re-raised below
            box["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout)
    assert not thread.is_alive(), f"did not return within {timeout}s"
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


@pytest.fixture
def hosts():
    """Start hosts on free local ports (guarded against hangs); stop them all
    after the test."""
    started: list[ComponentHost] = []
    channels: list[grpc.Channel] = []

    def start(host: ComponentHost, address: str = "127.0.0.1:0", serving=True):
        started.append(host)
        bound = _within(lambda: host.start(address))
        channel = grpc.insecure_channel(bound)
        channels.append(channel)
        if serving:
            _wait_serving(channel)
        return channel

    yield start
    for channel in channels:
        channel.close()
    for host in started:
        _within(lambda h=host: h.stop(grace=0), timeout=30)


def _aggregate_host(answer, **kwargs) -> tuple[ComponentHost, _ScriptedRouter]:
    router = _ScriptedRouter(answer)
    host = ComponentHost(router, **kwargs)
    host.add_aggregate(SimpleNamespace(domain="counter"))
    return host, router


def _command(domain: str = "counter"):
    command = builders.increase_command(1)
    command.command.cover.domain = domain
    return command


def _loop_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == _LOOP_THREAD]


# --- worker pool -------------------------------------------------------------


def test_dispatch_runs_on_the_hosts_dispatch_workers(hosts):
    seen: list[str] = []

    def answer(kind, *args):
        seen.append(threading.current_thread().name)
        return _RESPONSES[kind]()

    host, _router = _aggregate_host(answer)
    channel = hosts(host)
    command_handler_pb2_grpc.CommandHandlerServiceStub(channel).Handle(
        _command(), timeout=5
    )
    assert len(seen) == 1
    assert re.fullmatch(r"angzarr-dispatch_\d+", seen[0]), seen


@pytest.mark.parametrize(
    ("kwargs", "calls", "bound"),
    [({}, 20, 16), ({"max_workers": 2}, 5, 2)],
    ids=["default-16", "explicit-2"],
)
def test_max_workers_bounds_concurrent_dispatches(hosts, kwargs, calls, bound):
    gate = _Gate()
    host, _router = _aggregate_host(gate, **kwargs)
    stub = command_handler_pb2_grpc.CommandHandlerServiceStub(hosts(host))
    futures = [stub.Handle.future(_command(), timeout=20) for _ in range(calls)]
    try:
        assert _until(lambda: gate.active >= bound)
        time.sleep(0.3)
        assert (gate.active, gate.peak) == (bound, bound)
    finally:
        gate.release.set()
    assert all(f.result() == command_handler_pb2.BusinessResponse() for f in futures)
    assert gate.peak == bound


def test_stop_waits_for_a_running_dispatch_even_with_no_grace(hosts):
    gate = _Gate()
    host, _router = _aggregate_host(gate)
    stub = command_handler_pb2_grpc.CommandHandlerServiceStub(hosts(host))
    future = stub.Handle.future(_command(), timeout=10)
    assert gate.entered.wait(5)
    stopper = threading.Thread(target=host.stop, kwargs={"grace": 0}, daemon=True)
    stopper.start()
    try:
        with pytest.raises(grpc.RpcError) as err:
            future.result()
        assert err.value.code() == grpc.StatusCode.UNAVAILABLE
        time.sleep(0.3)
        assert stopper.is_alive(), "stop returned while a dispatch was running"
    finally:
        gate.release.set()
    stopper.join(10)
    stopped_at = time.monotonic()
    assert not stopper.is_alive()
    assert len(gate.finished_at) == 1 and gate.finished_at[0] <= stopped_at


def test_stop_lets_an_in_flight_call_finish_and_stays_not_serving(monkeypatch, hosts):
    monkeypatch.setenv("ANGZARR_READINESS_PROBE_INTERVAL", "0.02")
    gate = _Gate()
    host, _router = _aggregate_host(gate)
    stub = command_handler_pb2_grpc.CommandHandlerServiceStub(hosts(host))
    future = stub.Handle.future(_command(), timeout=5)
    assert gate.entered.wait(5)
    before = len(host.health_history(""))
    stopper = threading.Thread(target=host.stop, kwargs={"grace": 5}, daemon=True)
    stopper.start()
    time.sleep(0.3)
    gate.release.set()
    assert future.result() == command_handler_pb2.BusinessResponse()
    stopper.join(10)
    assert not stopper.is_alive()
    during_stop = host.health_history("")[before:]
    first_down = during_stop.index(_NOT_SERVING)
    assert during_stop[first_down:] == [_NOT_SERVING]


def _two_calls_racing_the_grace(stub, gate):
    """Put two calls in flight: one releasable at 'early', one at 'late'."""
    gate.releases = {"early": threading.Event(), "late": threading.Event()}
    early = stub.Handle.future(_command("early"), timeout=20)
    late = stub.Handle.future(_command("late"), timeout=20)
    assert _until(lambda: gate.active == 2)
    return early, late


def _release_around_five_seconds(gate) -> None:
    threading.Timer(4.5, gate.releases["early"].set).start()
    threading.Timer(5.5, gate.releases["late"].set).start()


def _outcome(future) -> grpc.StatusCode:
    try:
        future.result()
    except grpc.RpcError as err:
        return err.code()
    return grpc.StatusCode.OK


def test_stop_grace_defaults_to_five_seconds(hosts):
    gate = _Gate()
    host, _router = _aggregate_host(gate)
    stub = command_handler_pb2_grpc.CommandHandlerServiceStub(hosts(host))
    early, late = _two_calls_racing_the_grace(stub, gate)
    _release_around_five_seconds(gate)
    _within(host.stop, timeout=15)
    assert (_outcome(early), _outcome(late)) == (
        grpc.StatusCode.OK,
        grpc.StatusCode.UNAVAILABLE,
    )


# --- dispatch failures and the logger ---------------------------------------


def test_a_dispatch_failure_is_logged_through_the_hosts_logger(hosts):
    logger = _RecordingLogger()

    def answer(kind, *args):
        raise RuntimeError("boom")

    host, _router = _aggregate_host(answer, logger=logger)
    stub = command_handler_pb2_grpc.CommandHandlerServiceStub(hosts(host))
    with pytest.raises(grpc.RpcError):
        stub.Handle(_command(), timeout=5)
    assert logger.records == [("error", "dispatch_failed", {"error": "boom"})]


@pytest.fixture
def structlog_config():
    saved = structlog.get_config()
    yield
    structlog.configure(**saved)


def test_the_default_logger_is_the_host_modules_structlog_logger(
    hosts, structlog_config
):
    names: list[tuple] = []
    events: list[dict] = []

    def factory(*args):
        names.append(args)
        return structlog.ReturnLogger()

    def capture(logger, method, event_dict):
        events.append(dict(event_dict))
        return event_dict

    structlog.configure(
        processors=[capture], logger_factory=factory, cache_logger_on_first_use=False
    )

    def answer(kind, *args):
        raise RuntimeError("boom")

    host, _router = _aggregate_host(answer)
    stub = command_handler_pb2_grpc.CommandHandlerServiceStub(hosts(host))
    with pytest.raises(grpc.RpcError):
        stub.Handle(_command(), timeout=5)
    assert names == [("angzarr_client.host",)]
    assert events == [{"event": "dispatch_failed", "error": "boom"}]


# --- every servicer reports a coded failure ---------------------------------

_FAILED = CodedError(
    code="SCRIPTED_FAILURE",
    message="scripted failure",
    grpc=GrpcCode.FAILED_PRECONDITION,
    extras={"k": "v"},
)


def _fail(kind, *args):
    raise _FAILED


def _register(kind: str, host: ComponentHost) -> None:
    dispatch = SimpleNamespace(domain="counter")
    getattr(host, f"add_{kind}")(dispatch)


_CALLS = {
    "handle": (
        "aggregate",
        lambda ch: command_handler_pb2_grpc.CommandHandlerServiceStub(ch).Handle(
            _command(), timeout=5
        ),
    ),
    "handle_fact": (
        "aggregate",
        lambda ch: command_handler_pb2_grpc.CommandHandlerServiceStub(ch).HandleFact(
            command_handler_pb2.FactRequest(), timeout=5
        ),
    ),
    "replay": (
        "aggregate",
        lambda ch: command_handler_pb2_grpc.CommandHandlerServiceStub(ch).Replay(
            command_handler_pb2.ReplayRequest(), timeout=5
        ),
    ),
    "saga": (
        "saga",
        lambda ch: saga_pb2_grpc.SagaServiceStub(ch).Handle(
            saga_pb2.SagaHandleRequest(), timeout=5
        ),
    ),
    "process_manager": (
        "process_manager",
        lambda ch: process_manager_pb2_grpc.ProcessManagerServiceStub(ch).Handle(
            process_manager_pb2.ProcessManagerHandleRequest(), timeout=5
        ),
    ),
    "projector": (
        "projector",
        lambda ch: projector_pb2_grpc.ProjectorServiceStub(ch).Handle(
            types_pb2.EventBook(), timeout=5
        ),
    ),
    "projector_speculative": (
        "projector",
        lambda ch: projector_pb2_grpc.ProjectorServiceStub(ch).HandleSpeculative(
            types_pb2.EventBook(), timeout=5
        ),
    ),
}


@pytest.mark.parametrize("call", sorted(_CALLS))
def test_every_rpc_reports_a_coded_router_failure_as_its_status(hosts, call):
    kind, invoke = _CALLS[call]
    host = ComponentHost(_ScriptedRouter(_fail))
    _register(kind, host)
    channel = hosts(host)
    with pytest.raises(grpc.RpcError) as err:
        invoke(channel)
    assert err.value.code() == grpc.StatusCode.FAILED_PRECONDITION
    assert err.value.details() == "scripted failure"
    info = _error_info(err.value)
    assert (info.reason, dict(info.metadata)) == ("SCRIPTED_FAILURE", {"k": "v"})


def test_handle_speculative_dispatches_speculatively_and_handle_live(hosts):
    router = _ScriptedRouter(_empty_response)
    host = ComponentHost(router)
    _register("projector", host)
    stub = projector_pb2_grpc.ProjectorServiceStub(hosts(host))
    book = types_pb2.EventBook()
    book.cover.domain = "source"
    stub.HandleSpeculative(book, timeout=5)
    stub.Handle(book, timeout=5)
    assert [(kind, args[0].cover.domain) for kind, args in router.calls] == [
        ("dispatch_projector_speculative", "source"),
        ("dispatch_projector", "source"),
    ]


def test_replay_names_the_one_aggregates_domain(hosts):
    host, router = _aggregate_host(_empty_response)
    stub = command_handler_pb2_grpc.CommandHandlerServiceStub(hosts(host))
    request = command_handler_pb2.ReplayRequest(
        events=builders.prior_increases(1).pages
    )
    stub.Replay(request, timeout=5)
    assert [(kind, args[0]) for kind, args in router.calls] == [
        ("dispatch_replay", "counter")
    ]
    assert router.calls[0][1][1] == request


_UNIMPLEMENTED = CodedError(
    code="NO_HANDLER_REGISTERED", message="nothing", grpc=GrpcCode.UNIMPLEMENTED
)


def test_a_process_manager_acknowledges_an_unimplemented_dispatch(hosts):
    def answer(kind, *args):
        raise _UNIMPLEMENTED

    host = ComponentHost(_ScriptedRouter(answer))
    host.add_process_manager(SimpleNamespace())
    stub = process_manager_pb2_grpc.ProcessManagerServiceStub(hosts(host))
    resp = stub.Handle(process_manager_pb2.ProcessManagerHandleRequest(), timeout=5)
    assert resp == process_manager_pb2.ProcessManagerHandleResponse()


def test_an_aggregate_reports_an_unimplemented_dispatch(hosts):
    def answer(kind, *args):
        raise _UNIMPLEMENTED

    host, _router = _aggregate_host(answer)
    stub = command_handler_pb2_grpc.CommandHandlerServiceStub(hosts(host))
    with pytest.raises(grpc.RpcError) as err:
        stub.Handle(_command(), timeout=5)
    assert err.value.code() == grpc.StatusCode.UNIMPLEMENTED
    assert _error_info(err.value).reason == "NO_HANDLER_REGISTERED"


def test_a_saga_acknowledges_an_event_from_a_domain_it_does_not_consume(hosts):
    host = ComponentHost(Router())
    host.add_saga(order_saga_angzarr.new_order_saga_dispatch(OrderSaga()))
    stub = saga_pb2_grpc.SagaServiceStub(hosts(host))
    resp = stub.Handle(
        saga_pb2.SagaHandleRequest(source=_increased_book("elsewhere")), timeout=5
    )
    assert resp == saga_pb2.SagaResponse()


# --- state before start, exact configuration errors -------------------------


def test_an_unstarted_host_has_no_address_and_no_health():
    host = _counter_host()
    try:
        assert host.address == ""
        assert host.health_status() is None
        assert host.health_history() == []
    finally:
        host.stop()


def test_wait_returns_false_when_the_timeout_elapses():
    host = _counter_host()
    try:
        assert _within(lambda: host.wait(timeout=0.05), timeout=5) is False
    finally:
        host.stop()


def test_health_defaults_to_the_overall_server(hosts):
    host = _counter_host()
    hosts(host)
    assert host.health_status() == _SERVING
    assert host.health_history() == host.health_history("")
    assert host.health_history()[0] == _NOT_SERVING


def test_configuration_errors_name_the_problem():
    with pytest.raises(ConfigurationError) as err:
        _counter_host().add_service(lambda s, server: None, object(), "")
    assert str(err.value) == "an application service needs its full service name"
    host = _counter_host()
    _within(lambda: host.start("127.0.0.1:0"))
    try:
        with pytest.raises(ConfigurationError) as err:
            host.start("127.0.0.1:0")
        assert str(err.value) == "the host is already started"
    finally:
        _within(lambda: host.stop(grace=0))


def test_stop_after_a_failed_start_completes():
    host = _counter_host()
    with pytest.raises(Exception):  # noqa: B017 — grpc reports the bind failure
        _within(lambda: host.start("256.0.0.1:1"))
    _within(host.stop)
    assert host.wait(timeout=0)
    assert _loop_threads() == []


# --- event-loop thread and bound address -------------------------------------


def test_the_host_serves_from_one_named_daemon_loop_thread():
    # Started from the (non-daemon) main thread, so the loop thread's daemon
    # flag is the host's choice rather than inherited.
    assert threading.current_thread() is threading.main_thread()
    host = _counter_host()
    host.start("127.0.0.1:0")
    try:
        loops = _loop_threads()
        assert len(loops) == 1
        assert loops[0].daemon is True
    finally:
        _within(lambda: host.stop(grace=0))
    assert _loop_threads() == []


def test_an_ipv6_address_is_reported_with_its_bound_port(hosts):
    host = _counter_host()
    hosts(host, "[::1]:0")
    host_part, _, port = host.address.rpartition(":")
    assert host_part == "[::1]"
    assert int(port) > 0


# --- readiness probes ---------------------------------------------------------


@pytest.fixture
def fast_probes(monkeypatch):
    monkeypatch.setenv("ANGZARR_READINESS_PROBE_INTERVAL", "0.02")
    monkeypatch.setenv("ANGZARR_READINESS_PROBE_TIMEOUT", "0.2")
    monkeypatch.delenv("ANGZARR_BUS_ENDPOINT", raising=False)


@pytest.fixture
def unix_listener():
    sockets: list[socket.socket] = []

    def listen(path) -> str:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(path))
        sock.listen(64)
        sockets.append(sock)
        return str(path)

    yield listen
    for sock in sockets:
        sock.close()


def _saga_host() -> ComponentHost:
    host = ComponentHost(Router())
    host.add_saga(order_saga_angzarr.new_order_saga_dispatch(OrderSaga()))
    return host


def test_a_reachable_sync_output_domain_lets_the_host_serve(
    monkeypatch, fast_probes, unix_listener, tmp_path, hosts
):
    monkeypatch.setenv("ANGZARR_MODE", "standalone")
    monkeypatch.setenv("ANGZARR_UDS_BASE", str(tmp_path))
    unix_listener(tmp_path / "ch-orders.sock")
    host = _counter_host(sync_output_domains=["orders"])
    hosts(host, serving=False)
    assert _until(lambda: host.health_status("") == _SERVING)


def test_a_saga_host_waits_for_an_unreachable_bus(
    monkeypatch, fast_probes, tmp_path, hosts
):
    monkeypatch.setenv("ANGZARR_BUS_ENDPOINT", f"unix:{tmp_path}/bus.sock")
    host = _saga_host()
    hosts(host, serving=False)
    assert _until(lambda: len(host.health_history(SAGA_SERVICE)) >= 4)
    assert _SERVING not in host.health_history(SAGA_SERVICE)


def test_a_saga_host_serves_once_the_bus_is_reachable(
    monkeypatch, fast_probes, unix_listener, tmp_path, hosts
):
    bus = unix_listener(tmp_path / "bus.sock")
    monkeypatch.setenv("ANGZARR_BUS_ENDPOINT", f"unix:{bus}")
    host = _saga_host()
    hosts(host, serving=False)
    assert _until(lambda: host.health_status(SAGA_SERVICE) == _SERVING)


def test_an_aggregate_only_host_ignores_the_bus(
    monkeypatch, fast_probes, tmp_path, hosts
):
    monkeypatch.setenv("ANGZARR_BUS_ENDPOINT", f"unix:{tmp_path}/bus.sock")
    host = _counter_host()
    hosts(host, serving=False)
    assert _until(lambda: host.health_status(COMMAND_HANDLER_SERVICE) == _SERVING)


@pytest.fixture
def unresponsive_tcp_endpoint():
    """A loopback TCP listener whose accept queue is full: connects to it
    hang instead of failing."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(0)
    port = listener.getsockname()[1]
    fillers = []
    for _ in range(3):
        filler = socket.socket()
        filler.setblocking(False)
        try:
            filler.connect(("127.0.0.1", port))
        except BlockingIOError:
            pass
        fillers.append(filler)
    time.sleep(0.1)
    yield f"127.0.0.1:{port}"
    for sock in [*fillers, listener]:
        sock.close()


def test_a_hanging_probe_is_cut_off_by_the_probe_timeout(
    monkeypatch, unresponsive_tcp_endpoint, hosts
):
    monkeypatch.setenv("ANGZARR_READINESS_PROBE_INTERVAL", "0.02")
    monkeypatch.setenv("ANGZARR_READINESS_PROBE_TIMEOUT", "0.05")
    monkeypatch.setenv("ANGZARR_BUS_ENDPOINT", unresponsive_tcp_endpoint)
    host = _saga_host()
    hosts(host, serving=False)
    # Each timed-out tick republishes NOT_SERVING; a probe left hanging would
    # leave only the initial status.
    assert _until(lambda: len(host.health_history("")) >= 4, timeout=1.0)
    assert set(host.health_history("")) == {_NOT_SERVING}


# --- run() ---------------------------------------------------------------------


def _run_in_main_thread(host: ComponentHost, script, **run_kwargs) -> None:
    """Run ``host.run`` here (signals need the main thread) while ``script``
    drives it from a helper thread once the host is serving."""
    failures: list[BaseException] = []

    def drive():
        try:
            assert _until(lambda: host.address != "", timeout=10)
            channel = grpc.insecure_channel(host.address)
            try:
                _wait_serving(channel)
                script(channel)
            finally:
                channel.close()
        except BaseException as exc:  # noqa: BLE001 — reported below
            failures.append(exc)
            if not host.wait(timeout=0):
                os.kill(os.getpid(), signal.SIGTERM)

    driver = threading.Thread(target=drive, daemon=True)
    driver.start()
    host.run("127.0.0.1:0", **run_kwargs)
    driver.join(30)
    assert not driver.is_alive()
    if failures:
        raise failures[0]


def test_run_logs_start_and_shutdown_and_restores_signal_handlers():
    logger = _RecordingLogger()
    gate = _Gate()
    host, _router = _aggregate_host(gate, logger=logger)
    previous = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
    outcome: dict = {}

    def script(channel):
        stub = command_handler_pb2_grpc.CommandHandlerServiceStub(channel)
        future = stub.Handle.future(_command(), timeout=10)
        assert gate.entered.wait(5)
        os.kill(os.getpid(), signal.SIGTERM)
        try:
            outcome["code"] = _outcome(future)
        finally:
            gate.release.set()

    release_fallback = threading.Timer(3.0, gate.release.set)
    release_fallback.start()
    try:
        _run_in_main_thread(host, script, grace=0)
    finally:
        release_fallback.cancel()
        release_fallback.join(5)
    # grace=0 reaches stop(): the in-flight call is cut off, not awaited.
    assert outcome["code"] == grpc.StatusCode.UNAVAILABLE
    address = logger.records[0][2]["address"]
    assert address.startswith("127.0.0.1:")
    assert logger.records == [
        (
            "info",
            "server_started",
            {"services": [COMMAND_HANDLER_SERVICE], "address": address},
        ),
        ("info", "server_shutdown", {"services": [COMMAND_HANDLER_SERVICE]}),
    ]
    assert {s: signal.getsignal(s) for s in previous} == previous
    assert host.wait(timeout=0)


def test_run_grace_defaults_to_five_seconds():
    gate = _Gate()
    host, _router = _aggregate_host(gate)
    outcomes: list[grpc.StatusCode] = []

    def script(channel):
        stub = command_handler_pb2_grpc.CommandHandlerServiceStub(channel)
        early, late = _two_calls_racing_the_grace(stub, gate)
        os.kill(os.getpid(), signal.SIGTERM)
        _release_around_five_seconds(gate)
        outcomes.extend([_outcome(early), _outcome(late)])

    _run_in_main_thread(host, script)
    assert outcomes == [grpc.StatusCode.OK, grpc.StatusCode.UNAVAILABLE]


def test_run_stops_on_a_signal_delivered_to_another_thread():
    # The kernel may hand a process-directed SIGTERM to any thread; Python
    # runs the handler on the main thread only once that thread wakes.
    host = _counter_host()
    main = threading.main_thread().ident
    delivered: dict = {}
    # Fallback so a missed wakeup fails the test instead of hanging it;
    # cancelled once run() returns so it never signals a later test.
    fallback = threading.Timer(5.0, signal.pthread_kill, args=(main, signal.SIGTERM))

    def script(channel):
        delivered["at"] = time.monotonic()
        signal.pthread_kill(threading.get_ident(), signal.SIGTERM)
        fallback.start()

    try:
        _run_in_main_thread(host, script)
    finally:
        fallback.cancel()
        if fallback.is_alive():
            fallback.join(5)
    assert time.monotonic() - delivered["at"] < 3.0
    assert host.wait(timeout=0)


def test_run_honours_a_signal_that_arrives_while_the_host_is_starting():
    # The handlers are installed before the server starts: a SIGTERM during
    # startup stops the host gracefully instead of hitting the default
    # disposition (process termination) or being lost.
    host = _counter_host()
    started = host.start

    def start_then_signal(address=None):
        bound = started(address)
        os.kill(os.getpid(), signal.SIGTERM)
        return bound

    host.start = start_then_signal
    host.run("127.0.0.1:0", grace=0)
    assert host.wait(timeout=0)
    assert host.health_status("") == _NOT_SERVING


def test_run_with_a_failing_start_raises_and_restores_the_signal_handlers():
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    logger = _RecordingLogger()
    host = ComponentHost(logger=logger)
    with pytest.raises(ConfigurationError):
        host.run("127.0.0.1:0")
    assert {sig: signal.getsignal(sig) for sig in previous} == previous
    assert host.wait(timeout=0)
    assert logger.records == []
