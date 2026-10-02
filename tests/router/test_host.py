"""ComponentHost over gRPC with the router conformance fixture components
(tests/router/gen, rendered from angzarr-router's conformance protos)."""

from __future__ import annotations

import os
import signal
import threading

import grpc
import pytest
from google.rpc import error_details_pb2, status_pb2
from grpc_health.v1 import health_pb2, health_pb2_grpc

from angzarr_client import ComponentHost, ConfigurationError, PassThroughUpcaster
from angzarr_client.error_codes import codes, messages
from angzarr_client.host import (
    COMMAND_HANDLER_SERVICE,
    PROCESS_MANAGER_SERVICE,
    PROJECTOR_SERVICE,
    SAGA_SERVICE,
    UPCASTER_SERVICE,
    grpc_status_code,
)
from angzarr_client.proto.io.angzarr.v1 import (
    command_handler_pb2,
    command_handler_pb2_grpc,
    process_manager_pb2,
    process_manager_pb2_grpc,
    projector_pb2_grpc,
    saga_pb2,
    saga_pb2_grpc,
    types_pb2,
    upcaster_pb2,
    upcaster_pb2_grpc,
)
from angzarr_client.router import (
    AggregateDispatch,
    CodedError,
    GrpcCode,
    Rebuilder,
    Router,
)

from . import builders
from .fixture import CounterAggregate, CounterProjector, OrderProcessManager, OrderSaga
from .gen.test.counter import (
    counter_aggregate_angzarr,
    counter_pb2,
    counter_projector_angzarr,
    order_process_manager_angzarr,
    order_saga_angzarr,
)

_SERVING = health_pb2.HealthCheckResponse.SERVING
_NOT_SERVING = health_pb2.HealthCheckResponse.NOT_SERVING


@pytest.fixture
def started():
    """Start a host on a free local port; stop every started host after the test."""
    hosts: list[ComponentHost] = []
    channels: list[grpc.Channel] = []

    def start(host: ComponentHost):
        hosts.append(host)
        address = host.start("127.0.0.1:0")
        channel = grpc.insecure_channel(address)
        channels.append(channel)
        _wait_serving(channel)
        return channel

    yield start
    for channel in channels:
        channel.close()
    for host in hosts:
        host.stop(grace=0)


def _wait_serving(channel, service: str = "") -> None:
    stub = health_pb2_grpc.HealthStub(channel)
    for _ in range(200):
        status = stub.Check(
            health_pb2.HealthCheckRequest(service=service), timeout=5
        ).status
        if status == _SERVING:
            return
        threading.Event().wait(0.02)
    raise AssertionError(f"{service!r} never SERVING")


def _counter_host(**kwargs) -> ComponentHost:
    host = ComponentHost(Router(), **kwargs)
    host.add_aggregate(
        counter_aggregate_angzarr.new_counter_aggregate_dispatch(CounterAggregate())
    )
    return host


def _error_info(err: grpc.RpcError) -> error_details_pb2.ErrorInfo:
    raw = dict(err.trailing_metadata())["grpc-status-details-bin"]
    status = status_pb2.Status.FromString(raw)
    assert status.code == err.code().value[0]
    assert status.message == err.details()
    info = error_details_pb2.ErrorInfo()
    assert status.details[0].Unpack(info)
    return info


def _increased_book(domain: str, n: int = 1) -> types_pb2.EventBook:
    book = builders.prior_increases(n)
    book.cover.CopyFrom(builders.cover_of(domain, f"{domain}-1"))
    return book


def _unconsumed_book(domain: str) -> types_pb2.EventBook:
    book = types_pb2.EventBook()
    book.cover.CopyFrom(builders.cover_of(domain, f"{domain}-1"))
    page = book.pages.add()
    page.header.sequence = 0
    page.event.type_url = builders.type_url("test.counter.NotConsumed")
    return book


# --- CommandHandlerService ---------------------------------------------------


def test_handle_returns_the_routers_response(started):
    channel = started(_counter_host())
    stub = command_handler_pb2_grpc.CommandHandlerServiceStub(channel)
    resp = stub.Handle(builders.increase_command(2), timeout=5)
    assert [builders.fq_from_url(p.event.type_url) for p in resp.events.pages] == [
        builders.FQ_INCREASED,
        builders.FQ_INCREASED,
    ]


def test_a_coded_rejection_travels_as_status_and_error_info(started):
    channel = started(_counter_host())
    stub = command_handler_pb2_grpc.CommandHandlerServiceStub(channel)
    with pytest.raises(grpc.RpcError) as err:
        stub.Handle(builders.increase_command(0), timeout=5)
    assert err.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    info = _error_info(err.value)
    assert info.reason == "VALUE_NOT_POSITIVE"
    assert info.domain
    assert "VALUE_NOT_POSITIVE" not in err.value.details()


def test_an_unclassified_handler_failure_is_a_coded_internal_error(started):
    channel = started(_counter_host())
    stub = command_handler_pb2_grpc.CommandHandlerServiceStub(channel)
    with pytest.raises(grpc.RpcError) as err:
        stub.Handle(builders.fail_hard_command(), timeout=5)
    assert _error_info(err.value).reason == "UNHANDLED_HANDLER_ERROR"


def test_handle_fact_without_a_fact_handler_is_refused(started):
    channel = started(_counter_host())
    stub = command_handler_pb2_grpc.CommandHandlerServiceStub(channel)
    request = command_handler_pb2.FactRequest(facts=_increased_book("counter"))
    with pytest.raises(grpc.RpcError) as err:
        stub.HandleFact(request, timeout=5)
    assert err.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert _error_info(err.value).reason


def test_replay_rebuilds_the_one_aggregates_state(started):
    channel = started(_counter_host())
    stub = command_handler_pb2_grpc.CommandHandlerServiceStub(channel)
    request = command_handler_pb2.ReplayRequest(
        events=builders.prior_increases(3).pages
    )
    resp = stub.Replay(request, timeout=5)
    state = counter_pb2.CounterState()
    assert resp.state.Unpack(state)
    assert state.count == 3


def test_replay_with_several_aggregates_is_unsupported(started):
    host = _counter_host()
    host.add_aggregate(
        AggregateDispatch("Other", "other", Rebuilder(counter_pb2.CounterState))
    )
    channel = started(host)
    stub = command_handler_pb2_grpc.CommandHandlerServiceStub(channel)
    with pytest.raises(grpc.RpcError) as err:
        stub.Replay(command_handler_pb2.ReplayRequest(), timeout=5)
    assert err.value.code() == grpc.StatusCode.UNIMPLEMENTED
    assert err.value.details() == messages.HANDLER_DOES_NOT_SUPPORT_REPLAY
    info = _error_info(err.value)
    assert info.reason == codes.HANDLER_DOES_NOT_SUPPORT_REPLAY
    assert info.metadata["domains"] == "counter,other"


# --- Saga / process manager / projector / upcaster ---------------------------


def _saga_host() -> ComponentHost:
    host = ComponentHost(Router())
    host.add_saga(order_saga_angzarr.new_order_saga_dispatch(OrderSaga()))
    return host


def test_saga_handle_returns_the_deferred_commands(started):
    channel = started(_saga_host())
    stub = saga_pb2_grpc.SagaServiceStub(channel)
    resp = stub.Handle(
        saga_pb2.SagaHandleRequest(source=_increased_book("order")), timeout=5
    )
    assert len(resp.commands) == 1
    assert resp.commands[0].cover.domain == "inventory"


def test_saga_acknowledges_an_event_it_does_not_consume(started):
    channel = started(_saga_host())
    stub = saga_pb2_grpc.SagaServiceStub(channel)
    resp = stub.Handle(
        saga_pb2.SagaHandleRequest(source=_unconsumed_book("order")), timeout=5
    )
    assert resp == saga_pb2.SagaResponse()


def test_saga_reports_a_malformed_request(started):
    channel = started(_saga_host())
    stub = saga_pb2_grpc.SagaServiceStub(channel)
    with pytest.raises(grpc.RpcError) as err:
        stub.Handle(saga_pb2.SagaHandleRequest(), timeout=5)
    assert err.value.code() != grpc.StatusCode.UNIMPLEMENTED
    assert _error_info(err.value).reason


def _pm_host() -> ComponentHost:
    host = ComponentHost(Router())
    host.add_process_manager(
        order_process_manager_angzarr.new_order_process_manager_dispatch(
            OrderProcessManager()
        )
    )
    return host


def test_process_manager_handle_returns_its_response(started):
    channel = started(_pm_host())
    stub = process_manager_pb2_grpc.ProcessManagerServiceStub(channel)
    request = process_manager_pb2.ProcessManagerHandleRequest(
        trigger=_increased_book("counter")
    )
    resp = stub.Handle(request, timeout=5)
    assert len(resp.commands) == 1
    assert resp.commands[0].cover.domain == "inventory"


def test_process_manager_acknowledges_an_event_it_does_not_consume(started):
    channel = started(_pm_host())
    stub = process_manager_pb2_grpc.ProcessManagerServiceStub(channel)
    request = process_manager_pb2.ProcessManagerHandleRequest(
        trigger=_unconsumed_book("counter")
    )
    resp = stub.Handle(request, timeout=5)
    assert resp == process_manager_pb2.ProcessManagerHandleResponse()


def test_projector_handle_and_speculative_return_the_projection(started):
    host = ComponentHost(Router())
    host.add_projector(
        counter_projector_angzarr.new_counter_projector_dispatch(CounterProjector())
    )
    channel = started(host)
    stub = projector_pb2_grpc.ProjectorServiceStub(channel)
    book = _increased_book("counter", 2)
    for call in (stub.Handle, stub.HandleSpeculative):
        projection = call(book, timeout=5)
        assert projection.projector == "counter-projector"
        assert projection.sequence == 2


class _FailingUpcaster:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def upcast(self, request):
        raise self.error


def _upcaster_host(upcaster) -> ComponentHost:
    return _counter_host().add_upcaster(upcaster)


def test_pass_through_upcaster_returns_the_events_unchanged(started):
    channel = started(_upcaster_host(PassThroughUpcaster()))
    stub = upcaster_pb2_grpc.UpcasterServiceStub(channel)
    events = builders.prior_increases(2).pages
    resp = stub.Upcast(upcaster_pb2.UpcastRequest(events=events), timeout=5)
    assert list(resp.events) == list(events)


def test_an_upcaster_coded_error_travels_as_its_status(started):
    error = CodedError(
        code="LEGACY_SHAPE",
        message="unknown legacy shape",
        grpc=GrpcCode.FAILED_PRECONDITION,
        extras={"k": "v"},
    )
    channel = started(_upcaster_host(_FailingUpcaster(error)))
    stub = upcaster_pb2_grpc.UpcasterServiceStub(channel)
    with pytest.raises(grpc.RpcError) as err:
        stub.Upcast(upcaster_pb2.UpcastRequest(), timeout=5)
    assert err.value.code() == grpc.StatusCode.FAILED_PRECONDITION
    assert err.value.details() == "unknown legacy shape"
    info = _error_info(err.value)
    assert (info.reason, dict(info.metadata)) == ("LEGACY_SHAPE", {"k": "v"})


def test_an_unexpected_exception_is_internal_handler_panicked(started):
    channel = started(_upcaster_host(_FailingUpcaster(RuntimeError("boom"))))
    stub = upcaster_pb2_grpc.UpcasterServiceStub(channel)
    with pytest.raises(grpc.RpcError) as err:
        stub.Upcast(upcaster_pb2.UpcastRequest(), timeout=5)
    assert err.value.code() == grpc.StatusCode.INTERNAL
    assert err.value.details() == messages.HANDLER_PANICKED
    info = _error_info(err.value)
    assert (info.reason, info.metadata["error"]) == (codes.HANDLER_PANICKED, "boom")


# --- services, health and lifecycle ------------------------------------------


def test_services_are_the_registered_kinds_then_application_services():
    host = _counter_host()
    host.add_saga(order_saga_angzarr.new_order_saga_dispatch(OrderSaga()))
    host.add_process_manager(
        order_process_manager_angzarr.new_order_process_manager_dispatch(
            OrderProcessManager()
        )
    )
    host.add_projector(
        counter_projector_angzarr.new_counter_projector_dispatch(CounterProjector())
    )
    host.add_upcaster(PassThroughUpcaster())
    host.add_service(lambda s, server: None, object(), "app.ReportService")
    assert host.services == [
        COMMAND_HANDLER_SERVICE,
        SAGA_SERVICE,
        PROCESS_MANAGER_SERVICE,
        PROJECTOR_SERVICE,
        UPCASTER_SERVICE,
        "app.ReportService",
    ]
    assert COMMAND_HANDLER_SERVICE == "io.angzarr.v1.CommandHandlerService"
    host.stop()


def test_every_served_service_reports_serving_after_not_serving(started):
    host = _counter_host().add_upcaster(PassThroughUpcaster())
    channel = started(host)
    for name in ("", COMMAND_HANDLER_SERVICE, UPCASTER_SERVICE):
        _wait_serving(channel, name)
        history = host.health_history(name)
        assert history[0] == _NOT_SERVING and history[-1] == _SERVING
    assert host.health_status(SAGA_SERVICE) is None


def test_an_unreachable_sync_output_domain_keeps_the_host_not_serving(
    monkeypatch, started
):
    monkeypatch.setenv("ANGZARR_READINESS_PROBE_INTERVAL", "0.05")
    monkeypatch.setenv("ANGZARR_READINESS_PROBE_TIMEOUT", "0.05")
    host = _counter_host(sync_output_domains=["unreachable"])
    address = host.start("127.0.0.1:0")
    try:
        threading.Event().wait(0.3)
        assert host.health_status("") == _NOT_SERVING
        assert _SERVING not in host.health_history("")
        assert address.startswith("127.0.0.1:")
    finally:
        host.stop(grace=0)


def test_stop_reports_not_serving_and_refuses_new_calls():
    host = _counter_host()
    address = host.start("127.0.0.1:0")
    channel = grpc.insecure_channel(address)
    try:
        _wait_serving(channel)
        host.stop(grace=0)
        assert host.health_status("") == _NOT_SERVING
        assert host.health_status(COMMAND_HANDLER_SERVICE) == _NOT_SERVING
        assert host.wait(timeout=0)
        with pytest.raises(grpc.RpcError):
            command_handler_pb2_grpc.CommandHandlerServiceStub(channel).Handle(
                builders.increase_command(1), timeout=2
            )
        host.stop()  # idempotent
    finally:
        channel.close()


def test_an_owned_router_is_closed_on_stop():
    host = ComponentHost()
    host.add_aggregate(
        counter_aggregate_angzarr.new_counter_aggregate_dispatch(CounterAggregate())
    )
    host.start("127.0.0.1:0")
    host.stop(grace=0)
    with pytest.raises(Exception):  # noqa: B017 — a closed router refuses dispatch
        host.router.dispatch(builders.increase_command(1))


def test_a_borrowed_router_stays_open_after_stop():
    router = Router()
    host = ComponentHost(router)
    host.add_aggregate(
        counter_aggregate_angzarr.new_counter_aggregate_dispatch(CounterAggregate())
    )
    host.start("127.0.0.1:0")
    host.stop(grace=0)
    try:
        assert len(router.dispatch(builders.increase_command(1)).events.pages) == 1
    finally:
        router.close()


def test_stopping_an_unstarted_owning_host_closes_its_router():
    host = ComponentHost()
    host.stop()
    assert host.wait(timeout=0)
    with pytest.raises(Exception):  # noqa: B017
        host.router.dispatch(builders.increase_command(1))


def test_start_refuses_an_upcaster_only_host_and_a_second_start():
    host = ComponentHost(Router()).add_upcaster(PassThroughUpcaster())
    with pytest.raises(ConfigurationError, match=codes.NO_COMPONENTS_REGISTERED):
        host.start("127.0.0.1:0")
    host = _counter_host()
    host.start("127.0.0.1:0")
    try:
        with pytest.raises(ConfigurationError, match="already started"):
            host.start("127.0.0.1:0")
    finally:
        host.stop(grace=0)


def test_a_failed_bind_leaves_the_host_restartable():
    host = _counter_host()
    with pytest.raises(Exception):  # noqa: B017 — grpc reports the bind failure
        host.start("256.0.0.1:1")
    address = host.start("127.0.0.1:0")
    host.stop(grace=0)
    assert address.startswith("127.0.0.1:")


def test_add_service_requires_a_service_name():
    with pytest.raises(ConfigurationError, match="full service name"):
        _counter_host().add_service(lambda s, server: None, object(), "")


def test_start_uses_the_environment_transport(monkeypatch, tmp_path):
    monkeypatch.setenv("TRANSPORT_TYPE", "uds")
    monkeypatch.setenv("UDS_BASE_PATH", str(tmp_path))
    monkeypatch.setenv("SERVICE_NAME", "host")
    for var in ("DOMAIN", "SAGA_NAME", "PROJECTOR_NAME"):
        monkeypatch.delenv(var, raising=False)
    host = _counter_host()
    address = host.start()
    sock = tmp_path / "host.sock"
    try:
        assert address == f"unix:{sock}"
        assert sock.exists()
        channel = grpc.insecure_channel(address)
        resp = command_handler_pb2_grpc.CommandHandlerServiceStub(channel).Handle(
            builders.increase_command(1), timeout=5
        )
        channel.close()
        assert len(resp.events.pages) == 1
    finally:
        host.stop(grace=0)
    assert not sock.exists()


def test_run_serves_until_sigterm():
    host = _counter_host()
    previous = signal.getsignal(signal.SIGTERM)

    def signal_once_serving():
        for _ in range(500):
            if host.address:
                os.kill(os.getpid(), signal.SIGTERM)
                return
            threading.Event().wait(0.01)

    sender = threading.Thread(target=signal_once_serving, daemon=True)
    sender.start()
    host.run("127.0.0.1:0", grace=0)
    sender.join(5)
    assert host.wait(timeout=0)
    assert host.health_status("") == _NOT_SERVING
    assert signal.getsignal(signal.SIGTERM) is previous


def test_grpc_status_code_maps_numbers_and_defaults_to_internal():
    assert grpc_status_code(3) == grpc.StatusCode.INVALID_ARGUMENT
    assert grpc_status_code(12) == grpc.StatusCode.UNIMPLEMENTED
    assert grpc_status_code(999) == grpc.StatusCode.INTERNAL
