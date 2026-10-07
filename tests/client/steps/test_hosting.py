"""Step defs for parity/client/hosting.feature: the generic ComponentHost
serving components registered with the router binding over gRPC.

The "order" / "payment" aggregate components are framework-only fixtures:
hand-registered AggregateDispatch tables whose state and command are
well-known protobuf types, recording which component each command reached.
"""

from __future__ import annotations

import os
import pathlib
import threading
from dataclasses import dataclass, field

import grpc
import pytest
from google.protobuf import wrappers_pb2
from grpc_health.v1 import health_pb2, health_pb2_grpc
from pytest_bdd import given, parsers, scenarios, then, when

from angzarr_client import ComponentHost, ConfigurationError
from angzarr_client.proto.io.angzarr.v1 import command_handler_pb2_grpc, types_pb2
from angzarr_client.router import AggregateDispatch, Rebuilder, Router, pack

scenarios("parity/client/hosting.feature")

COMMAND_HANDLER_SERVICE = "io.angzarr.v1.CommandHandlerService"
REPORT_SERVICE = "test.hosting.OrderReportService"


@dataclass
class _World:
    tmp: pathlib.Path
    reached: list[tuple[str, str]] = field(default_factory=list)
    host: ComponentHost | None = None
    address: str = ""
    start_error: Exception | None = None
    socket_path: pathlib.Path | None = None
    release: threading.Event = field(default_factory=threading.Event)
    entered: threading.Event = field(default_factory=threading.Event)
    in_flight: list = field(default_factory=list)
    status_at_shutdown: int | None = None
    channels: list[grpc.Channel] = field(default_factory=list)

    def channel(self) -> grpc.Channel:
        target = self.address
        ch = grpc.insecure_channel(target)
        self.channels.append(ch)
        return ch


@pytest.fixture
def world(tmp_path, monkeypatch):
    for var in (
        "TRANSPORT_TYPE",
        "UDS_BASE_PATH",
        "SERVICE_NAME",
        "DOMAIN",
        "SAGA_NAME",
        "PROJECTOR_NAME",
        "PORT",
        "ANGZARR_BIND_ADDRESS",
    ):
        monkeypatch.delenv(var, raising=False)
    w = _World(tmp=tmp_path)
    yield w
    w.release.set()
    for ch in w.channels:
        ch.close()
    if w.host is not None:
        w.host.stop(grace=0)


def _aggregate(world: _World, domain: str, block: bool = False) -> AggregateDispatch:
    dispatch = AggregateDispatch(
        name=f"{domain}-component",
        domain=domain,
        rebuilder=Rebuilder(wrappers_pb2.Int64Value),
    )

    def handle(command, state, cctx):
        world.reached.append(
            (domain, wrappers_pb2.StringValue.FromString(command.value).value)
        )
        if block:
            world.entered.set()
            world.release.wait(timeout=10)
        book = types_pb2.EventBook()
        book.pages.add().event.CopyFrom(pack(wrappers_pb2.Int64Value(value=1)))
        return book

    dispatch.on_command("google.protobuf.StringValue", handle)
    return dispatch


def _command(domain: str, text: str) -> types_pb2.ContextualCommand:
    cc = types_pb2.ContextualCommand()
    cc.command.cover.domain = domain
    cc.command.cover.root.value = bytes(16)
    page = cc.command.pages.add()
    page.header.sequence = 0
    page.command.CopyFrom(pack(wrappers_pb2.StringValue(value=text)))
    return cc


def _new_host(world: _World, domains: list[str], block: bool = False) -> ComponentHost:
    world.host = ComponentHost(Router())
    for domain in domains:
        world.host.add_aggregate(_aggregate(world, domain, block=block))
    return world.host


def _health(world: _World, service: str) -> int:
    stub = health_pb2_grpc.HealthStub(world.channel())
    return stub.Check(health_pb2.HealthCheckRequest(service=service), timeout=5).status


def _wait_serving(world: _World, service: str = "") -> None:
    status = None
    for _ in range(100):
        status = _health(world, service)
        if status == health_pb2.HealthCheckResponse.SERVING:
            return
        threading.Event().wait(0.05)
    raise AssertionError(f"health for {service!r} never became SERVING (last {status})")


def _socket_for(world: _World, feature_path: str) -> pathlib.Path:
    """The private socket standing in for the feature's path."""
    return world.tmp / pathlib.Path(feature_path).name


# --- Given -----------------------------------------------------------------


@given(
    parsers.parse(
        'a component host with an aggregate component for domain "{domain}" registered'
    )
)
def _host_one(world: _World, domain: str) -> None:
    _new_host(world, [domain])


@given(
    parsers.parse(
        'a component host with aggregate components for domains "{first}" and "{second}" registered'
    )
)
def _host_two(world: _World, first: str, second: str) -> None:
    _new_host(world, [first, second])


@given("a component host with no components registered")
def _host_none(world: _World) -> None:
    world.host = ComponentHost(Router())


@given(
    parsers.parse(
        'a started component host with an aggregate component for domain "{domain}" registered'
    )
)
def _host_started(world: _World, domain: str) -> None:
    _new_host(world, [domain], block=True)
    world.address = world.host.start(
        None if os.environ.get("TRANSPORT_TYPE") else "127.0.0.1:0"
    )
    _wait_serving(world)


@given(
    parsers.re(
        r'the transport environment selects (?P<transport>TCP|Unix socket) at "(?P<address>[^"]+)"'
    )
)
def _transport_env(world: _World, monkeypatch, transport: str, address: str) -> None:
    if transport == "TCP":
        monkeypatch.setenv("TRANSPORT_TYPE", "tcp")
        monkeypatch.setenv("ANGZARR_BIND_ADDRESS", address)
    else:
        path = _socket_for(world, address)
        world.socket_path = path
        monkeypatch.setenv("TRANSPORT_TYPE", "uds")
        monkeypatch.setenv("UDS_BASE_PATH", str(path.parent))
        monkeypatch.setenv("SERVICE_NAME", path.stem)


@given(
    parsers.parse("an application-defined gRPC service {name} registered on the host")
)
def _app_service(world: _World, name: str) -> None:
    def report(request: bytes, context) -> bytes:
        return b"report:" + request

    handler = grpc.method_handlers_generic_handler(
        REPORT_SERVICE,
        {"Report": grpc.unary_unary_rpc_method_handler(report)},
    )
    world.host.add_service(
        lambda h, server: server.add_generic_rpc_handlers((h,)), handler, REPORT_SERVICE
    )


# --- When ------------------------------------------------------------------


@when("the host starts on a TCP port")
def _start_tcp(world: _World) -> None:
    world.address = world.host.start("127.0.0.1:0")


@when("the host starts")
def _start_env(world: _World) -> None:
    try:
        world.address = world.host.start()
    except ConfigurationError as exc:
        world.start_error = exc


@when("shutdown begins")
def _shutdown_begins(world: _World) -> None:
    stub = command_handler_pb2_grpc.CommandHandlerServiceStub(world.channel())
    call = stub.Handle.future(_command("order", "in-flight"), timeout=10)
    world.in_flight.append(call)
    assert world.entered.wait(
        timeout=5
    ), "the in-flight call never reached the component"
    stopper = threading.Thread(target=world.host.stop, kwargs={"grace": 10})
    stopper.start()
    for _ in range(100):
        world.status_at_shutdown = world.host.health_status("")
        if world.status_at_shutdown == health_pb2.HealthCheckResponse.NOT_SERVING:
            break
        threading.Event().wait(0.02)
    world.in_flight.append(stopper)


@when("the host shuts down")
def _shuts_down(world: _World) -> None:
    world.host.stop(grace=1)


# --- Then ------------------------------------------------------------------


@then("CommandHandlerService is served on that port")
def _ch_served(world: _World) -> None:
    resp = command_handler_pb2_grpc.CommandHandlerServiceStub(world.channel()).Handle(
        _command("order", "probe"), timeout=5
    )
    assert len(resp.events.pages) == 1


@then(
    parsers.re(
        r'a ContextualCommand for domain "(?P<domain>\w+)"( sent to CommandHandlerService.Handle)? '
        r"reaches the (?P<component>\w+) component"
    )
)
def _reaches(world: _World, domain: str, component: str) -> None:
    text = f"to-{domain}"
    resp = command_handler_pb2_grpc.CommandHandlerServiceStub(world.channel()).Handle(
        _command(domain, text), timeout=5
    )
    assert len(resp.events.pages) == 1
    assert (component, text) in world.reached
    assert all(d == component for d, t in world.reached if t == text)


@then(
    parsers.re(
        r'the host listens on (?P<transport>TCP|Unix socket) at "(?P<address>[^"]+)"'
    )
)
def _listens(world: _World, transport: str, address: str) -> None:
    if transport == "TCP":
        host, _, port = world.address.rpartition(":")
        assert host == address.rpartition(":")[0]
        assert port.isdigit() and int(port) > 0
    else:
        path = _socket_for(world, address)
        assert world.address == f"unix:{path}"
        assert path.exists()
    _ch_served(world)


@then(
    "the overall server's health status is SERVING only after every registered "
    "component's service is listening"
)
def _serving_after_listening(world: _World) -> None:
    assert (
        world.host.health_history("")[0] == health_pb2.HealthCheckResponse.NOT_SERVING
    )
    _wait_serving(world)
    _ch_served(world)


@then(parsers.parse("the gRPC health service reports SERVING for {service}"))
def _serving_for(world: _World, service: str) -> None:
    names = {
        "CommandHandlerService": COMMAND_HANDLER_SERVICE,
        "OrderReportService": REPORT_SERVICE,
    }
    _wait_serving(world, names[service])


@then("the gRPC health service reports NOT_SERVING for the overall server")
def _not_serving(world: _World) -> None:
    assert world.status_at_shutdown == health_pb2.HealthCheckResponse.NOT_SERVING


@then("in-flight calls complete before the server stops")
def _in_flight_complete(world: _World) -> None:
    call, stopper = world.in_flight
    assert stopper.is_alive(), "the server stopped while a call was in flight"
    world.release.set()
    resp = call.result(timeout=10)
    assert len(resp.events.pages) == 1
    stopper.join(timeout=15)
    assert not stopper.is_alive()


@then(parsers.parse('"{path}" no longer exists'))
def _socket_removed(world: _World, path: str) -> None:
    sock = _socket_for(world, path)
    assert world.address == f"unix:{sock}"
    assert not sock.exists()


@then(parsers.parse("{name} is served on that port alongside CommandHandlerService"))
def _app_served(world: _World, name: str) -> None:
    call = world.channel().unary_unary(f"/{REPORT_SERVICE}/Report")
    assert call(b"x", timeout=5) == b"report:x"
    _ch_served(world)


@then("starting fails with a configuration error")
def _config_error(world: _World) -> None:
    assert isinstance(world.start_error, ConfigurationError)
    assert "no components" in str(world.start_error)
