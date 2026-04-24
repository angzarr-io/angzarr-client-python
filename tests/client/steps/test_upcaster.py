"""Step defs for features/client/upcaster.feature.

Verifies the @upcaster / @upcasts / @state_factory decorators are callable
with valid attribute shapes. Full Router/dispatch integration is exercised
elsewhere; this feature pins the symbol + attribute surface only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from pytest_bdd import given, parsers, scenarios, then

from angzarr_client import state_factory, upcaster, upcasts

scenarios("upcaster.feature")


@dataclass
class _State:
    cls: Any = None
    fn: Any = None
    errors: list = field(default_factory=list)


@pytest.fixture
def state() -> _State:
    return _State()


class _FromType:
    pass


class _ToType:
    pass


@given(
    parsers.re(
        r'a class "(?P<cls_name>[^"]+)" decorated as an upcaster named'
        r' "(?P<up_name>[^"]+)" in domain "(?P<domain>[^"]+)"'
    )
)
def _given_upcaster_class(
    state: _State, cls_name: str, up_name: str, domain: str
) -> None:
    try:

        @upcaster(name=up_name, domain=domain)
        class UpcasterCls:
            pass

        UpcasterCls.__name__ = cls_name
        state.cls = UpcasterCls
    except Exception as exc:
        state.errors.append(exc)


@given(
    parsers.re(
        r'a method declared as upcasting from "(?P<from_name>[^"]+)"'
        r' to "(?P<to_name>[^"]+)"'
    )
)
def _given_upcasts_method(state: _State, from_name: str, to_name: str) -> None:
    try:

        @upcasts(_FromType, _ToType)
        def upgrade(old):
            return old

        state.fn = upgrade
    except Exception as exc:
        state.errors.append(exc)


@given("a method declared as a state factory")
def _given_state_factory_method(state: _State) -> None:
    try:

        @state_factory
        def empty_state():
            return object()

        state.fn = empty_state
    except Exception as exc:
        state.errors.append(exc)


@then("the class declaration compiles without error")
def _then_class_compiles(state: _State) -> None:
    assert not state.errors, f"declaration raised: {state.errors}"
    assert state.cls is not None


@then("the method declaration compiles without error")
def _then_method_compiles(state: _State) -> None:
    assert not state.errors, f"declaration raised: {state.errors}"
    assert state.fn is not None
