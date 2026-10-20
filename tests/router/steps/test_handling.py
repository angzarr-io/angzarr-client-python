"""The handler-contract tier's one-component scenario (angzarr-project
features/handler-contract/handling.feature, C-0580), bound against the
binding's Router. The rest of that tier is bound by angzarr-router's own
conformance suite."""

from __future__ import annotations

import pathlib

import pytest
from pytest_bdd import given, parsers, scenario, then, when

from angzarr_client.proto.io.angzarr.v1 import types_pb2
from angzarr_client.router import (
    AggregateDispatch,
    CodedError,
    GrpcCode,
    ProcessManagerDispatch,
    ProjectorDispatch,
    Rebuilder,
    Router,
    SagaDispatch,
)

from ..builders import FQ_INCREASED
from ..gen.test.counter import counter_pb2

_FEATURE = (
    pathlib.Path(__file__).resolve().parents[3]
    / "angzarr-project"
    / "features"
    / "handler-contract"
    / "handling.feature"
)


@scenario(
    str(_FEATURE),
    "A business process refuses to register a second component of any kind",
)
def test_c0580_a_second_component_of_any_kind_is_refused():
    pass


def _rebuilder() -> Rebuilder:
    return Rebuilder(factory=counter_pb2.CounterState)


_REGISTER = {
    "aggregate": lambda r, name: r.register_aggregate(
        AggregateDispatch(name, name, _rebuilder())
    ),
    "saga": lambda r, name: r.register_saga(
        SagaDispatch(name, "order").on_event(FQ_INCREASED, lambda _e, _c: [])
    ),
    "process manager": lambda r, name: r.register_process_manager(
        ProcessManagerDispatch(name, "fulfillment", _rebuilder())
    ),
    "projector": lambda r, name: r.register_projector(
        ProjectorDispatch(name, counter_pb2.CounterProjectorState)
    ),
}


class _World:
    def __init__(self) -> None:
        self.router = Router()
        self.version = ""
        self.refusal: CodedError | None = None


@pytest.fixture
def world():
    w = _World()
    yield w
    w.router.close()


@given(parsers.parse('an "{binary}" business binary built as version "{version}"'))
def _built_as(world: _World, binary: str, version: str) -> None:
    world.version = version


@given(
    parsers.parse(
        'the "{binary}" business binary has its "{name}" aggregate registered'
    )
)
def _registered(world: _World, binary: str, name: str) -> None:
    world.router.register_aggregate(
        AggregateDispatch(name, name, _rebuilder()).with_rules_version(world.version)
    )


@when(
    parsers.parse(
        'a {kind} component "{name}" is registered in the same business process'
    )
)
def _second(world: _World, kind: str, name: str) -> None:
    with pytest.raises(CodedError) as exc:
        _REGISTER[kind](world.router, name)
    world.refusal = exc.value


@then(
    parsers.parse(
        'the registration is refused, naming the "{name}" aggregate already registered'
    )
)
def _refused(world: _World, name: str) -> None:
    assert world.refusal.code == "DUPLICATE_REGISTRATION"
    assert world.refusal.grpc == GrpcCode.INVALID_ARGUMENT
    assert world.refusal.extras["component"] == name


@then(parsers.parse('the business process describes only the "{name}" aggregate'))
def _describes_only(world: _World, name: str) -> None:
    hello = world.router.component_descriptor()
    assert hello.name == name
    assert hello.kind == types_pb2.ComponentKind.COMPONENT_KIND_AGGREGATE
