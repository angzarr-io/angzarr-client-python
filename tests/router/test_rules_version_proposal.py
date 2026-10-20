"""Item-4 proposal, rules version (docs/design/handling-and-command-modes.md):
the business binary's build version — baked in at build time by versionator
(``versionator output emit python`` writes ``__version__``) — given once to
the host, which stamps it on every component it hosts, so every answer and
the registration hello carry it (decisions 14.1, 14.5, 14.6;
handling.feature C-0533..C-0535).

Strict xfails until the owner signs the proposal off (see
test_handling_proposal.py for the convention).
"""

from __future__ import annotations

import pytest

from angzarr_client import ComponentHost, ConfigurationError
from angzarr_client.proto.io.angzarr.v1 import (
    command_handler_pb2_grpc,
    process_manager_pb2,
    process_manager_pb2_grpc,
)
from angzarr_client.router import Router

from . import builders
from .fixture import CounterAggregate, OrderProcessManager
from .gen.test.counter import (
    counter_aggregate_angzarr,
    order_process_manager_angzarr,
)
from .test_host import _increased_book, started  # noqa: F401 — the fixture

PENDING = pytest.mark.xfail(
    strict=True,
    reason="item-4 proposal (build-baked rules version) pending owner sign-off",
)

# What a versionator-emitted ``_version.__version__`` holds for a build.
BUILD_VERSION = "1.4.2+build.7"


def _host(**kwargs) -> ComponentHost:
    host = ComponentHost(Router(), **kwargs)
    host.add_aggregate(
        counter_aggregate_angzarr.new_counter_aggregate_dispatch(CounterAggregate())
    )
    host.add_process_manager(
        order_process_manager_angzarr.new_order_process_manager_dispatch(
            OrderProcessManager()
        )
    )
    return host


@PENDING
def test_c0533_every_answer_reports_the_build_version(started):  # noqa: F811
    channel = started(_host(rules_version=BUILD_VERSION))
    stub = command_handler_pb2_grpc.CommandHandlerServiceStub(channel)
    assert stub.Handle(builders.increase_command(1), timeout=5).rules_version == (
        BUILD_VERSION
    )


@PENDING
def test_c0534_a_process_managers_answer_reports_its_build_version(
    started,  # noqa: F811
):
    channel = started(_host(rules_version=BUILD_VERSION))
    stub = process_manager_pb2_grpc.ProcessManagerServiceStub(channel)
    request = process_manager_pb2.ProcessManagerHandleRequest(
        trigger=_increased_book("counter")
    )
    assert stub.Handle(request, timeout=5).rules_version == BUILD_VERSION


@PENDING
def test_c0535_registration_announces_the_same_build_version():
    host = _host(rules_version=BUILD_VERSION)
    try:
        hellos = host.router.component_descriptors()
    finally:
        host.stop()

    assert [h.rules_version for h in hellos] == [BUILD_VERSION, BUILD_VERSION]
    assert hellos[0].state.type_url == "/test.counter.CounterState"


@PENDING
def test_decision_14_6_a_host_without_a_build_version_refuses_to_start():
    host = _host()
    try:
        with pytest.raises(ConfigurationError, match="MISSING_RULES_VERSION"):
            host.start("127.0.0.1:0")
    finally:
        host.stop()
