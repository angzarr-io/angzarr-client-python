"""A component written by hand (tests/router/hand.py's tables and a
descriptor from :func:`angzarr_client.describe`; no generated code, no hex)
launched as a child process and accepted by its parent.

The parent here does what angzarr-embedded-host's ``Host::start`` does to a
child (its crate is a library, with no binary to launch from these tests,
so the launch is tests/router/test_child.py's): read the handshake, call
``ComponentService.Describe`` over the socket it names, refuse a child of
another kind than configured and a descriptor that fails the coordinator's
rules (``descriptor::validate``, whose rules
``angzarr_client.descriptor._refusal`` keeps, in its order), then call the
kind service."""

from __future__ import annotations

import grpc
import pytest

from angzarr_client.descriptor import _refusal
from angzarr_client.proto.io.angzarr.v1 import (
    command_handler_pb2_grpc,
    component_pb2,
    component_pb2_grpc,
    policy_pb2_grpc,
    projector_pb2_grpc,
    types_pb2,
)
from angzarr_client.router import pack

from . import builders
from .gen import counter_pb2, order_pb2
from .test_child import Child

KINDS = {
    "aggregate": types_pb2.COMPONENT_KIND_AGGREGATE,
    "policy": types_pb2.COMPONENT_KIND_POLICY,
    "process_manager": types_pb2.COMPONENT_KIND_PROCESS_MANAGER,
    "projector": types_pb2.COMPONENT_KIND_PROJECTOR,
}


def _book(domain: str) -> types_pb2.EventBook:
    book = types_pb2.EventBook()
    book.cover.domain = domain
    book.pages.add(sequence=0).payload.CopyFrom(pack(counter_pb2.Increased()))
    return book


def _call_kind_service(kind: str, channel) -> None:
    """One delivery to the child's kind service, as the coordinator makes."""
    if kind == "aggregate":
        stub = command_handler_pb2_grpc.CommandHandlerServiceStub(channel)
        assert stub.Handle(builders.increase_command(1), timeout=10).events.pages
    elif kind == "process_manager":
        request = types_pb2.ContextualCommand()
        request.command.page.command.payload.CopyFrom(
            pack(order_pb2.StartFulfillment())
        )
        stub = command_handler_pb2_grpc.CommandHandlerServiceStub(channel)
        assert stub.Handle(request, timeout=10).events.pages
    elif kind == "policy":
        stub = policy_pb2_grpc.PolicyServiceStub(channel)
        assert stub.Handle(_book("alpha"), timeout=10).reactions
    else:
        stub = projector_pb2_grpc.ProjectorServiceStub(channel)
        assert stub.Handle(_book("beta"), timeout=10).projector == "Totals"


@pytest.mark.parametrize("kind", list(KINDS))
def test_a_hand_written_child_is_described_accepted_and_served(kind, tmp_path):
    child = Child(str(tmp_path / "hand.sock"), kind, module="child_hand")
    try:
        with grpc.insecure_channel(child.address) as channel:
            stub = component_pb2_grpc.ComponentServiceStub(channel)
            descriptor = stub.Describe(component_pb2.DescribeRequest(), timeout=10)
            assert descriptor.kind == KINDS[kind]
            assert _refusal(descriptor) is None
            assert descriptor.rules_version == builders.TEST_RULES_VERSION
            _call_kind_service(kind, channel)
        code, rest, err = child.finish()
        assert code == 0, err
        assert rest == ""
    finally:
        child.kill()


def test_the_parent_would_refuse_a_descriptor_that_breaks_a_rule():
    # The check above is the coordinator's: a hand-built descriptor with no
    # rules version, served unchanged, is refused.
    descriptor = types_pb2.ComponentDescriptor(
        name="Totals", kind=types_pb2.COMPONENT_KIND_PROJECTOR
    )
    assert _refusal(descriptor) == (
        "COORDINATOR_REASON_MISSING_RULES_VERSION",
        {"component": "Totals"},
    )
