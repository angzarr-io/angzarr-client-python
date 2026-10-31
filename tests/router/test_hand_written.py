"""A projector, a policy and a process manager written by hand: no
angzarr-cli output, only the router's dispatch tables, ordinary handler
functions and :func:`angzarr_client.describe`, each hosted by a
ComponentHost and called over gRPC as its coordinator would.

Handlers are synchronous: the router calls them directly and the host runs
dispatch on its worker pool (no handler in this package is async)."""

from __future__ import annotations

import grpc
import pytest

from angzarr_client import ComponentHost, describe
from angzarr_client.proto.io.angzarr.v1 import (
    command_handler_pb2_grpc,
    component_pb2,
    component_pb2_grpc,
    policy_pb2,
    policy_pb2_grpc,
    projector_pb2_grpc,
    types_pb2,
)
from angzarr_client.router import pack

from .gen import counter_pb2, order_pb2
from .hand import fulfilment, reserve, reserving, totals
from .test_host import started  # noqa: F401 — the fixture

VERSION = "2.0.0+hand"


def _book(domain: str, *events, first: int = 0) -> types_pb2.EventBook:
    book = types_pb2.EventBook()
    book.cover.domain = domain
    for offset, event in enumerate(events):
        book.pages.add(sequence=first + offset).payload.CopyFrom(pack(event))
    return book


def _describe_call(channel) -> types_pb2.ComponentDescriptor:
    stub = component_pb2_grpc.ComponentServiceStub(channel)
    return stub.Describe(component_pb2.DescribeRequest(), timeout=5)


# --- a projector over several source domains -----------------------------------


@pytest.fixture
def projector_channel(started):  # noqa: F811
    table = totals()
    host = ComponentHost(rules_version=VERSION)
    host.add_projector(table, describe(table, rules_version=VERSION))
    return started(host)


def test_a_hand_written_projector_describes_each_source_domain(projector_channel):
    assert _describe_call(projector_channel) == types_pb2.ComponentDescriptor(
        name="Totals",
        kind=types_pb2.COMPONENT_KIND_PROJECTOR,
        rules_version=VERSION,
        inputs=[types_pb2.Target(domain="alpha"), types_pb2.Target(domain="beta")],
    )


@pytest.mark.parametrize("domain", ["alpha", "beta"])
def test_a_hand_written_projector_folds_a_book_of_each_source(
    projector_channel, domain
):
    stub = projector_pb2_grpc.ProjectorServiceStub(projector_channel)
    book = _book(domain, counter_pb2.Increased(), counter_pb2.Increased(), first=4)
    projection = stub.Handle(book, timeout=5)
    assert projection.projector == "Totals"
    assert projection.cover.domain == domain
    assert projection.next_sequence == 6
    view = counter_pb2.CounterProjectorState.FromString(projection.projection.value)
    assert view.count == 2


def test_a_hand_written_projector_folds_nothing_of_another_domain(projector_channel):
    stub = projector_pb2_grpc.ProjectorServiceStub(projector_channel)
    projection = stub.Handle(_book("gamma", counter_pb2.Increased()), timeout=5)
    view = counter_pb2.CounterProjectorState.FromString(projection.projection.value)
    assert view.count == 0


# --- a policy ------------------------------------------------------------------


@pytest.fixture
def policy_channel(started):  # noqa: F811
    table = reserving()
    host = ComponentHost(rules_version=VERSION)
    host.add_policy(table, describe(table, rules_version=VERSION))
    return started(host)


def test_a_hand_written_policy_describes_its_input_and_output(policy_channel):
    assert _describe_call(policy_channel) == types_pb2.ComponentDescriptor(
        name="Reserving",
        kind=types_pb2.COMPONENT_KIND_POLICY,
        rules_version=VERSION,
        inputs=[types_pb2.Target(domain="alpha")],
        output_domain="beta",
    )


def test_a_hand_written_policy_answers_its_commands_by_trigger(policy_channel):
    stub = policy_pb2_grpc.PolicyServiceStub(policy_channel)
    book = _book("alpha", counter_pb2.Increased(), counter_pb2.Increased(), first=7)
    response = stub.Handle(book, timeout=5)
    assert [r.trigger_sequence for r in response.reactions] == [7, 8]
    assert [c.cover.domain for r in response.reactions for c in r.commands] == [
        "beta",
        "beta",
    ]


def test_a_hand_written_policy_translates_a_refusal(policy_channel):
    stub = policy_pb2_grpc.PolicyServiceStub(policy_channel)
    refusal = policy_pb2.Refusal()
    refusal.command.CopyFrom(reserve("beta"))
    response = stub.OnRefused(refusal, timeout=5)
    fact = response.fact.page.fact.payload
    assert fact.type_url == "/orders.v1.StockUnavailable"
    assert order_pb2.StockUnavailable.FromString(fact.value).item == "x"


# --- a process manager ---------------------------------------------------------


@pytest.fixture
def pm_channel(started):  # noqa: F811
    table = fulfilment()
    host = ComponentHost(rules_version=VERSION)
    host.add_process_manager(
        table,
        describe(
            table, rules_version=VERSION, starting_types=[order_pb2.StartFulfillment]
        ),
    )
    return started(host)


def test_a_hand_written_process_manager_describes_its_state_and_starts(pm_channel):
    described = _describe_call(pm_channel)
    expected = types_pb2.ComponentDescriptor(
        name="Fulfilment",
        kind=types_pb2.COMPONENT_KIND_PROCESS_MANAGER,
        rules_version=VERSION,
        starting_types=["orders.v1.StartFulfillment"],
    )
    expected.state.type_url = "/orders.v1.FulfillmentState"
    expected.state.options.conflict_resolutions.extend(
        [
            types_pb2.CONFLICT_RESOLUTION_STRICT,
            types_pb2.CONFLICT_RESOLUTION_AGGREGATE_HANDLES,
        ]
    )
    assert described == expected


def test_a_hand_written_process_manager_handles_its_starting_command(pm_channel):
    stub = command_handler_pb2_grpc.CommandHandlerServiceStub(pm_channel)
    request = types_pb2.ContextualCommand()
    request.command.cover.domain = "fulfilment"
    request.command.page.command.payload.CopyFrom(pack(order_pb2.StartFulfillment()))
    response = stub.Handle(request, timeout=5)
    assert [p.payload.type_url for p in response.events.pages] == [
        "/orders.v1.FulfillmentStarted"
    ]
    assert response.rules_version == VERSION
    state = order_pb2.FulfillmentState()
    response.events.snapshot.state.Unpack(state)
    assert state.placed == 1


def test_a_hand_written_process_manager_ingests_a_fact(pm_channel):
    stub = command_handler_pb2_grpc.CommandHandlerServiceStub(pm_channel)
    request = types_pb2.ContextualCommand()
    request.command.cover.domain = "fulfilment"
    request.command.page.fact.payload.CopyFrom(pack(order_pb2.ItemDelivered()))
    response = stub.Handle(request, timeout=5)
    assert [p.payload.type_url for p in response.events.pages] == [
        "/orders.v1.ItemDelivered",
        "/orders.v1.FulfillmentStarted",
    ]


def test_an_unhandled_command_is_refused_with_its_reason(pm_channel):
    stub = command_handler_pb2_grpc.CommandHandlerServiceStub(pm_channel)
    request = types_pb2.ContextualCommand()
    request.command.page.command.payload.CopyFrom(pack(order_pb2.ShipOrder()))
    with pytest.raises(grpc.RpcError) as err:
        stub.Handle(request, timeout=5)
    assert err.value.details() == "ROUTER_REASON_UNKNOWN_COMMAND_TYPE"
