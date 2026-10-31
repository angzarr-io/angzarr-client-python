"""Registration: the business binary's build version — baked in at build
time by versionator (``versionator output emit python`` writes
``__version__``) — given once to the host, which stamps it on the one
component it hosts, so every answer and the component's description carry it
(decisions 14.1, 14.5, 14.10; handling.feature C-0533, C-0534). The
coordinator pulls the description and judges it (decision 14.42): the host
answers ``ComponentService.Describe`` with the descriptor angzarr-cli
generated, given with the component, unchanged."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from angzarr_client import ComponentHost, ConfigurationError, describe
from angzarr_client.error_codes import codes
from angzarr_client.host import COMPONENT_SERVICE
from angzarr_client.proto.io.angzarr.v1 import (
    command_handler_pb2_grpc,
    component_pb2,
    component_pb2_grpc,
    types_pb2,
)
from angzarr_client.router import Router, pack

from . import builders
from .contract import (
    counter_aggregate,
    counter_projector,
    described,
    fulfillment,
    order_policy,
)
from .gen import order_pb2
from .test_host import started  # noqa: F401 — the fixture

# What a versionator-emitted ``_version.__version__`` holds for a build.
BUILD_VERSION = "1.4.2+build.7"


def _aggregate_host(**kwargs) -> ComponentHost:
    host = ComponentHost(Router(), **kwargs)
    host.add_aggregate(*described(counter_aggregate([]), host.rules_version))
    return host


def _pm_host(**kwargs) -> ComponentHost:
    host = ComponentHost(Router(), **kwargs)
    host.add_process_manager(*described(fulfillment("", False, []), host.rules_version))
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


def test_describe_answers_the_generated_descriptor_unchanged(started):  # noqa: F811
    # Decision 14.42: angzarr-cli generates the descriptor; the business
    # process answers Describe with it unchanged, and the coordinator
    # validates it (here even a starting type on an aggregate stands).
    generated = types_pb2.ComponentDescriptor(
        name="CounterAggregate",
        kind=types_pb2.COMPONENT_KIND_AGGREGATE,
        rules_version=BUILD_VERSION,
        inputs=[types_pb2.Target(domain="counter")],
        starting_types=["test.counter.IncreaseBy"],
    )
    generated.state.type_url = "/test.counter.CounterState"
    host = ComponentHost(Router(), rules_version=BUILD_VERSION)
    host.add_aggregate(counter_aggregate([]), generated)
    assert host.descriptor == generated
    assert _describe(started(host)) == generated


@pytest.mark.parametrize(
    "kind", ["aggregate", "policy", "process_manager", "projector"]
)
def test_a_component_is_registered_with_its_descriptor(kind):
    tables = {
        "aggregate": counter_aggregate([]),
        "policy": order_policy("inventory", []),
        "process_manager": fulfillment("", False, []),
        "projector": counter_projector([]),
    }
    with pytest.raises(TypeError):
        getattr(ComponentHost(Router()), f"add_{kind}")(tables[kind])


def test_a_host_with_no_component_has_no_descriptor():
    assert ComponentHost(Router()).descriptor is None


def test_component_service_is_served_first():
    host = _aggregate_host(rules_version=BUILD_VERSION)
    try:
        assert host.services[0] == COMPONENT_SERVICE
    finally:
        host.stop()


def test_a_table_with_its_own_version_keeps_it(started):  # noqa: F811
    host = ComponentHost(Router(), rules_version=BUILD_VERSION)
    host.add_aggregate(*described(counter_aggregate([]).with_rules_version("9.9.9")))
    stub = command_handler_pb2_grpc.CommandHandlerServiceStub(started(host))
    assert stub.Handle(builders.increase_command(1), timeout=5).rules_version == "9.9.9"


# --- the descriptor's rules version is the one every answer reports ---------------


def _tables():
    return {
        "aggregate": counter_aggregate([]),
        "policy": order_policy("inventory", []),
        "process_manager": fulfillment("", False, []),
        "projector": counter_projector([]),
    }


@pytest.mark.parametrize(
    "kind", ["aggregate", "policy", "process_manager", "projector"]
)
def test_a_descriptor_of_another_rules_version_is_refused(kind):
    table = _tables()[kind]
    host = ComponentHost(Router(), rules_version=BUILD_VERSION)
    described_as = describe(table, rules_version="0.0.1")
    with pytest.raises(ConfigurationError) as info:
        getattr(host, f"add_{kind}")(table, described_as)
    assert info.value.code == "DESCRIPTOR_RULES_VERSION_MISMATCH"
    assert str(info.value) == (
        "the descriptor's rules_version is not the version the component reports"
    )
    assert info.value.details == {
        "component": table.name,
        "described": "0.0.1",
        "reported": BUILD_VERSION,
    }
    # Nothing is registered: the host still has no component.
    assert host.descriptor is None
    with pytest.raises(ConfigurationError) as start:
        host.start("127.0.0.1:0")
    assert start.value.code == codes.HOST_REASON_NO_COMPONENTS_REGISTERED


@pytest.mark.parametrize(
    "kind", ["aggregate", "policy", "process_manager", "projector"]
)
def test_a_descriptor_of_the_hosts_rules_version_is_registered(kind):
    table = _tables()[kind]
    host = ComponentHost(Router(), rules_version=BUILD_VERSION)
    descriptor = describe(table, rules_version=BUILD_VERSION)
    getattr(host, f"add_{kind}")(table, descriptor)
    assert host.descriptor is descriptor
    host.stop()


def test_a_table_with_its_own_version_is_described_with_it():
    # The table's own version is the one its answers report, so it is the
    # one its descriptor must carry, whatever the host's.
    table = counter_aggregate([]).with_rules_version("9.9.9")
    host = ComponentHost(Router(), rules_version=BUILD_VERSION)
    with pytest.raises(ConfigurationError) as info:
        host.add_aggregate(table, describe(table, rules_version=BUILD_VERSION))
    assert info.value.details["reported"] == "9.9.9"
    host.add_aggregate(table, describe(table))
    assert host.descriptor.rules_version == "9.9.9"
    host.stop()


def test_a_descriptor_with_a_version_on_a_host_without_one_is_refused():
    # Answers would report no version, and the coordinator refuses an
    # answer reporting any version but the registered one.
    host = ComponentHost(Router())
    table = counter_aggregate([])
    with pytest.raises(ConfigurationError) as info:
        host.add_aggregate(table, describe(table, rules_version=BUILD_VERSION))
    assert info.value.details == {
        "component": "CounterAggregate",
        "described": BUILD_VERSION,
        "reported": "",
    }


def test_a_table_naming_no_component_or_version_is_refused_as_unnamed():
    # A hand-built table need only route; the host's version is the one it
    # reports.
    table = SimpleNamespace(kind=types_pb2.COMPONENT_KIND_AGGREGATE)
    host = ComponentHost(Router(), rules_version=BUILD_VERSION)
    with pytest.raises(ConfigurationError) as info:
        host.add_aggregate(table, types_pb2.ComponentDescriptor(rules_version="x"))
    assert info.value.details == {
        "component": "",
        "described": "x",
        "reported": BUILD_VERSION,
    }
