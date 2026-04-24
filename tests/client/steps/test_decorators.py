"""Step defs for features/client/decorators.feature.

Verifies class-level kind declarations produce the same HandlerConfig
shape across languages.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from pytest_bdd import given, parsers, scenarios, then

from angzarr_client import command_handler, saga

scenarios("decorators.feature")


@dataclass
class _State:
    cls: Any = None


@pytest.fixture
def state() -> _State:
    return _State()


@dataclass
class OrderState:
    pass


@given(
    parsers.re(
        r'a class "(?P<name>[^"]+)" decorated as a command handler for domain'
        r' "(?P<domain>[^"]+)" with state \S+'
    )
)
def _given_command_handler(state: _State, name: str, domain: str) -> None:
    @command_handler(domain=domain, state=OrderState)
    class Order:
        pass

    Order.__name__ = name
    state.cls = Order


@given(
    parsers.parse(
        'a class "{name}" decorated as a saga named "{saga_name}"'
        ' from "{source}" to "{target}"'
    )
)
def _given_saga(
    state: _State, name: str, saga_name: str, source: str, target: str
) -> None:
    @saga(name=saga_name, source=source, target=target)
    class SagaCls:
        pass

    SagaCls.__name__ = name
    state.cls = SagaCls


@then(parsers.parse('the class exposes a handler config of kind "{kind}"'))
def _then_kind(state: _State, kind: str) -> None:
    assert getattr(state.cls, "__angzarr_kind__", None) == kind


@then(parsers.parse('the handler config\'s domain is "{domain}"'))
def _then_domain(state: _State, domain: str) -> None:
    meta = getattr(state.cls, "__angzarr_meta__", {})
    assert meta.get("domain") == domain


@then(parsers.parse('the handler config\'s saga name is "{name}"'))
def _then_saga_name(state: _State, name: str) -> None:
    meta = getattr(state.cls, "__angzarr_meta__", {})
    assert meta.get("name") == name


@then(parsers.parse('the handler config\'s saga source is "{source}"'))
def _then_saga_source(state: _State, source: str) -> None:
    meta = getattr(state.cls, "__angzarr_meta__", {})
    assert meta.get("source") == source


@then(parsers.parse('the handler config\'s saga target is "{target}"'))
def _then_saga_target(state: _State, target: str) -> None:
    meta = getattr(state.cls, "__angzarr_meta__", {})
    assert meta.get("target") == target
