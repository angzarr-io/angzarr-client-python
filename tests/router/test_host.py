"""ComponentHost over gRPC with the handler-contract tier's components
(tests/router/contract.py)."""

from __future__ import annotations

import os
import signal
import threading

import grpc
import pytest
from google.rpc import error_details_pb2, status_pb2
from grpc_health.v1 import health_pb2, health_pb2_grpc

from angzarr_client import ComponentHost, ConfigurationError, PassThroughUpcaster
from angzarr_client.error_codes import codes
from angzarr_client.host import (
    COMMAND_HANDLER_SERVICE,
    COMPONENT_SERVICE,
    POLICY_SERVICE,
    PROJECTOR_SERVICE,
    UPCASTER_SERVICE,
    grpc_status_code,
)
from angzarr_client.proto.io.angzarr.v1 import (
    command_handler_pb2_grpc,
    policy_pb2,
    policy_pb2_grpc,
    projector_pb2_grpc,
    types_pb2,
    upcaster_pb2,
    upcaster_pb2_grpc,
)
from angzarr_client.router import CodedError, GrpcCode, Router, pack

from . import builders
from .builders import TEST_RULES_VERSION
from .contract import counter_aggregate, counter_projector, fulfillment, order_policy
from .gen import counter_pb2, order_pb2

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
    host = ComponentHost(Router(), rules_version=TEST_RULES_VERSION, **kwargs)
    host.add_aggregate(counter_aggregate([]))
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
    page.sequence = 0
    page.payload.type_url = builders.type_url("test.counter.NotConsumed")
    return book


# --- CommandHandlerService ---------------------------------------------------


def test_handle_returns_the_routers_response(started):
    channel = started(_counter_host())
    stub = command_handler_pb2_grpc.CommandHandlerServiceStub(channel)
    resp = stub.Handle(builders.increase_command(2), timeout=5)
    assert [builders.fq_from_url(p.payload.type_url) for p in resp.events.pages] == [
        builders.FQ_INCREASED,
        builders.FQ_INCREASED,
    ]


def test_a_coded_rejection_travels_as_status_and_error_info(started):
    channel = started(_counter_host())
    stub = command_handler_pb2_grpc.CommandHandlerServiceStub(channel)
    with pytest.raises(grpc.RpcError) as err:
        stub.Handle(builders.increase_command(0), timeout=5)
    assert err.value.code() == grpc.StatusCode.FAILED_PRECONDITION
    info = _error_info(err.value)
    assert info.reason == "VALUE_NOT_POSITIVE"
    assert info.domain


def test_an_unclassified_handler_failure_is_a_coded_internal_error(started):
    channel = started(_counter_host())
    stub = command_handler_pb2_grpc.CommandHandlerServiceStub(channel)
    with pytest.raises(grpc.RpcError) as err:
        stub.Handle(builders.fail_hard_command(), timeout=5)
    assert err.value.details() == "UNHANDLED_HANDLER_ERROR"
    info = _error_info(err.value)
    assert (info.reason, dict(info.metadata)) == ("UNHANDLED_HANDLER_ERROR", {})


def test_a_fact_without_a_fact_handler_is_refused(started):
    channel = started(_counter_host())
    stub = command_handler_pb2_grpc.CommandHandlerServiceStub(channel)
    request = builders.increase_command(1)
    request.command.page.ClearField("command")
    request.command.page.fact.payload.type_url = builders.type_url(
        builders.FQ_INCREASED
    )
    with pytest.raises(grpc.RpcError) as err:
        stub.Handle(request, timeout=5)
    assert err.value.code() == grpc.StatusCode.INTERNAL
    assert _error_info(err.value).reason == "NO_FACT_HANDLER"


# --- Policy / process manager / projector / upcaster -------------------------


def _policy_host() -> ComponentHost:
    host = ComponentHost(Router(), rules_version=TEST_RULES_VERSION)
    host.add_policy(order_policy("inventory", []))
    return host


def test_policy_handle_returns_the_commands_grouped_by_trigger(started):
    channel = started(_policy_host())
    stub = policy_pb2_grpc.PolicyServiceStub(channel)
    resp = stub.Handle(_increased_book("order", 2), timeout=5)
    assert [r.trigger_sequence for r in resp.reactions] == [0, 1]
    assert [c.cover.domain for r in resp.reactions for c in r.commands] == [
        "inventory",
        "inventory",
    ]


def test_policy_answers_no_reaction_to_an_event_it_does_not_consume(started):
    channel = started(_policy_host())
    stub = policy_pb2_grpc.PolicyServiceStub(channel)
    resp = stub.Handle(_unconsumed_book("order"), timeout=5)
    assert resp == policy_pb2.PolicyResponse()


def test_policy_on_refused_answers_no_translation(started):
    channel = started(_policy_host())
    stub = policy_pb2_grpc.PolicyServiceStub(channel)
    refusal = policy_pb2.Refusal()
    refusal.command.page.command.payload.type_url = builders.type_url(
        builders.FQ_RESERVE
    )
    assert stub.OnRefused(refusal, timeout=5) == policy_pb2.OnRefusedResponse()


def _pm_host() -> ComponentHost:
    host = ComponentHost(Router(), rules_version=TEST_RULES_VERSION)
    host.add_process_manager(fulfillment("", True, []))
    return host


def test_a_process_manager_is_a_domain_served_as_a_command_handler(started):
    channel = started(_pm_host())
    stub = command_handler_pb2_grpc.CommandHandlerServiceStub(channel)
    request = types_pb2.ContextualCommand()
    request.command.page.command.payload.CopyFrom(pack(order_pb2.StartFulfillment()))
    resp = stub.Handle(request, timeout=5)
    assert [builders.fq_from_url(p.payload.type_url) for p in resp.events.pages] == [
        "orders.v1.FulfillmentStarted"
    ]
    assert resp.rules_version == TEST_RULES_VERSION


def test_projector_handle_returns_the_projection(started):
    host = ComponentHost(Router(), rules_version=TEST_RULES_VERSION)
    host.add_projector(counter_projector([]))
    channel = started(host)
    stub = projector_pb2_grpc.ProjectorServiceStub(channel)
    book = _increased_book("counter", 2)
    projection = stub.Handle(book, timeout=5)
    assert projection.projector == "CounterProjector"
    assert projection.next_sequence == 2
    assert counter_pb2.CounterProjectorState.FromString(
        projection.projection.value
    ) == counter_pb2.CounterProjectorState(count=2)


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


def test_an_upcaster_coded_error_travels_as_its_key_and_args(started):
    error = CodedError("LEGACY_SHAPE", GrpcCode.FAILED_PRECONDITION, {"k": "v"})
    channel = started(_upcaster_host(_FailingUpcaster(error)))
    stub = upcaster_pb2_grpc.UpcasterServiceStub(channel)
    with pytest.raises(grpc.RpcError) as err:
        stub.Upcast(upcaster_pb2.UpcastRequest(), timeout=5)
    assert err.value.code() == grpc.StatusCode.FAILED_PRECONDITION
    # Decision 14.37: the status message IS the reason key.
    assert err.value.details() == "LEGACY_SHAPE"
    info = _error_info(err.value)
    assert (info.reason, dict(info.metadata)) == ("LEGACY_SHAPE", {"k": "v"})


def test_an_unexpected_exception_is_unhandled_caused_by_a_panic(started):
    # Decision 14.37: an escaped exception is UNHANDLED_HANDLER_ERROR with
    # cause HANDLER_PANICKED; its text stays in the host's own log.
    channel = started(_upcaster_host(_FailingUpcaster(RuntimeError("boom"))))
    stub = upcaster_pb2_grpc.UpcasterServiceStub(channel)
    with pytest.raises(grpc.RpcError) as err:
        stub.Upcast(upcaster_pb2.UpcastRequest(), timeout=5)
    assert err.value.code() == grpc.StatusCode.INTERNAL
    assert err.value.details() == codes.UNHANDLED_HANDLER_ERROR
    info = _error_info(err.value)
    assert (info.reason, dict(info.metadata)) == (
        codes.UNHANDLED_HANDLER_ERROR,
        {"cause": codes.HANDLER_PANICKED},
    )
    raw = dict(err.value.trailing_metadata())["grpc-status-details-bin"]
    assert b"boom" not in raw


def test_the_host_puts_no_catalog_text_on_the_wire():
    # error_codes.messages is display-only (decision 14.37, ER-4): the
    # host renders nothing from it.
    import angzarr_client.host as host_module

    assert not hasattr(host_module, "messages")


# --- services, health and lifecycle ------------------------------------------


def test_services_are_the_component_then_its_kind_then_application_services():
    host = _counter_host()
    host.add_upcaster(PassThroughUpcaster())
    host.add_service(lambda s, server: None, object(), "app.ReportService")
    assert host.services == [
        COMPONENT_SERVICE,
        COMMAND_HANDLER_SERVICE,
        UPCASTER_SERVICE,
        "app.ReportService",
    ]
    assert COMMAND_HANDLER_SERVICE == "io.angzarr.v1.CommandHandlerService"
    host.stop()


@pytest.mark.parametrize(
    ("kind", "service"),
    [
        ("aggregate", COMMAND_HANDLER_SERVICE),
        ("policy", POLICY_SERVICE),
        ("process_manager", COMMAND_HANDLER_SERVICE),
        ("projector", PROJECTOR_SERVICE),
    ],
)
def test_a_host_serves_its_one_component_kind(kind, service):
    host = ComponentHost(Router(), rules_version=TEST_RULES_VERSION)
    getattr(host, f"add_{kind}")(_TABLES[kind]())
    try:
        assert host.services == [COMPONENT_SERVICE, service]
    finally:
        host.stop()


_TABLES = {
    "aggregate": lambda: counter_aggregate([]),
    "policy": lambda: order_policy("inventory", []),
    "process_manager": lambda: fulfillment("", True, []),
    "projector": lambda: counter_projector([]),
}


@pytest.mark.parametrize("first", sorted(_TABLES))
@pytest.mark.parametrize("second", sorted(_TABLES))
def test_a_host_given_a_second_component_refuses_to_start(first, second):
    # C-0579: the second component is not hosted, and start() refuses,
    # naming both, before anything is served.
    host = ComponentHost(Router(), rules_version=TEST_RULES_VERSION)
    first_table, second_table = _TABLES[first](), _TABLES[second]()
    getattr(host, f"add_{first}")(first_table)
    getattr(host, f"add_{second}")(second_table)
    try:
        assert host.router.component_descriptor().name == first_table.name
        with pytest.raises(ConfigurationError) as exc:
            host.start("127.0.0.1:0")
        assert exc.value.code == codes.DUPLICATE_REGISTRATION
        assert str(exc.value) == codes.DUPLICATE_REGISTRATION
        assert exc.value.details == {
            "registered": first_table.name,
            "refused": second_table.name,
        }
        assert host.address == ""
        assert host.health_status() is None
    finally:
        host.stop()


def test_every_served_service_reports_serving_after_not_serving(started):
    host = _counter_host().add_upcaster(PassThroughUpcaster())
    channel = started(host)
    for name in ("", COMMAND_HANDLER_SERVICE, UPCASTER_SERVICE):
        _wait_serving(channel, name)
        history = host.health_history(name)
        assert history[0] == _NOT_SERVING and history[-1] == _SERVING
    assert host.health_status(POLICY_SERVICE) is None


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
    host = ComponentHost(rules_version=TEST_RULES_VERSION)
    host.add_aggregate(counter_aggregate([]))
    host.start("127.0.0.1:0")
    host.stop(grace=0)
    with pytest.raises(Exception):  # noqa: B017 — a closed router refuses dispatch
        host.router.dispatch(builders.increase_command(1))


def test_a_borrowed_router_stays_open_after_stop():
    router = Router()
    host = ComponentHost(router, rules_version=TEST_RULES_VERSION)
    host.add_aggregate(counter_aggregate([]))
    host.start("127.0.0.1:0")
    host.stop(grace=0)
    try:
        assert len(router.dispatch(builders.increase_command(1)).events.pages) == 1
    finally:
        router.close()


def test_stopping_an_unstarted_owning_host_closes_its_router():
    host = ComponentHost(rules_version=TEST_RULES_VERSION)
    host.stop()
    assert host.wait(timeout=0)
    with pytest.raises(Exception):  # noqa: B017
        host.router.dispatch(builders.increase_command(1))


def test_start_refuses_an_upcaster_only_host_and_a_second_start():
    host = ComponentHost(Router(), rules_version=TEST_RULES_VERSION).add_upcaster(
        PassThroughUpcaster()
    )
    with pytest.raises(ConfigurationError) as refused:
        host.start("127.0.0.1:0")
    assert (refused.value.code, str(refused.value)) == (
        codes.NO_COMPONENTS_REGISTERED,
        codes.NO_COMPONENTS_REGISTERED,
    )
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
