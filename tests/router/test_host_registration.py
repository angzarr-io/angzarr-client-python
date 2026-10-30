"""Registration: the business binary's build version — baked in at build
time by versionator (``versionator output emit python`` writes
``__version__``) — given once to the host, which stamps it on the one
component it hosts, so every answer and the component's description carry it
(decisions 14.1, 14.5, 14.10; handling.feature C-0533, C-0534). The
coordinator pulls the description and judges it (decision 14.42): the host
serves ``ComponentService.Describe`` from the router's descriptor."""

from __future__ import annotations

from angzarr_client import ComponentHost
from angzarr_client.host import COMPONENT_SERVICE
from angzarr_client.proto.io.angzarr.v1 import (
    command_handler_pb2_grpc,
    component_pb2,
    component_pb2_grpc,
    types_pb2,
)
from angzarr_client.router import Router, pack

from . import builders
from .contract import counter_aggregate, fulfillment, order_policy
from .gen import order_pb2
from .test_host import started  # noqa: F401 — the fixture

# What a versionator-emitted ``_version.__version__`` holds for a build.
BUILD_VERSION = "1.4.2+build.7"


def _aggregate_host(**kwargs) -> ComponentHost:
    host = ComponentHost(Router(), **kwargs)
    host.add_aggregate(counter_aggregate([]))
    return host


def _pm_host(**kwargs) -> ComponentHost:
    host = ComponentHost(Router(), **kwargs)
    host.add_process_manager(fulfillment("", False, []))
    return host


def _describe(channel):
    stub = component_pb2_grpc.ComponentServiceStub(channel)
    return stub.Describe(component_pb2.DescribeRequest(), timeout=5)


def test_c0533_every_answer_reports_the_build_version(started):  # noqa: F811
    channel = started(_aggregate_host(rules_version=BUILD_VERSION))
    stub = command_handler_pb2_grpc.CommandHandlerServiceStub(channel)
    assert stub.Handle(builders.increase_command(1), timeout=5).rules_version == (
        BUILD_VERSION
    )


def test_c0534_a_process_managers_answer_reports_its_build_version(
    started,  # noqa: F811
):
    channel = started(_pm_host(rules_version=BUILD_VERSION))
    stub = command_handler_pb2_grpc.CommandHandlerServiceStub(channel)
    request = types_pb2.ContextualCommand()
    request.command.page.command.payload.CopyFrom(pack(order_pb2.StartFulfillment()))
    assert stub.Handle(request, timeout=5).rules_version == BUILD_VERSION


def test_describing_the_process_announces_the_same_build_version(
    started,  # noqa: F811
):
    answer = _describe(started(_aggregate_host(rules_version=BUILD_VERSION)))

    described = answer
    assert described.name == "CounterAggregate"
    assert described.kind == types_pb2.ComponentKind.COMPONENT_KIND_AGGREGATE
    assert described.rules_version == BUILD_VERSION
    assert described.state.type_url == "/test.counter.CounterState"


def test_a_policy_host_describes_its_policy(started):  # noqa: F811
    host = ComponentHost(Router(), rules_version=BUILD_VERSION)
    host.add_policy(order_policy("inventory", []))
    described = _describe(started(host))

    assert (described.name, described.kind, described.state.type_url) == (
        "OrderPolicy",
        types_pb2.ComponentKind.COMPONENT_KIND_POLICY,
        "",
    )
    assert [t.domain for t in described.inputs] == ["order"]
    assert described.output_domain == "inventory"


def test_component_service_is_served_first():
    host = _aggregate_host(rules_version=BUILD_VERSION)
    try:
        assert host.services[0] == COMPONENT_SERVICE
    finally:
        host.stop()


def test_a_table_with_its_own_version_keeps_it(started):  # noqa: F811
    host = ComponentHost(Router(), rules_version=BUILD_VERSION)
    host.add_aggregate(counter_aggregate([]).with_rules_version("9.9.9"))
    described = _describe(started(host))
    assert described.rules_version == "9.9.9"


def test_a_host_without_a_build_version_describes_none_for_the_coordinator(
    started,  # noqa: F811
):
    # Decision 14.42: the coordinator, not the business process, refuses a
    # description with no version (MISSING_RULES_VERSION).
    assert _describe(started(_aggregate_host())).rules_version == ""
