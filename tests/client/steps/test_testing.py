"""Step defs for features/client/testing.feature.

Exercises the cross-language testing subpackage: deterministic UUIDs under
DEFAULT_TEST_NAMESPACE, builder shapes for Cover/EventPage/EventBook, and
ScenarioContext reset semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from angzarr_client.testing import (
    DEFAULT_TEST_NAMESPACE,
    ScenarioContext,
    make_cover,
    make_event_book,
    uuid_for,
    uuid_obj_for,
    uuid_str_for,
)

scenarios("../../parity/client/testing.feature")


@dataclass
class _State:
    uuid_bytes: bytes | None = None
    uuid_str: str | None = None
    uuid_obj: UUID | None = None
    derived_root: bytes | None = None
    cover: Any = None
    event_book: Any = None
    context: ScenarioContext | None = None


@pytest.fixture
def state() -> _State:
    return _State()


@then(parsers.parse('DEFAULT_TEST_NAMESPACE equals the UUID "{expected}"'))
def _then_namespace_equals(expected: str) -> None:
    assert str(DEFAULT_TEST_NAMESPACE) == expected


@when(parsers.re(r'I call uuid_for with the name "(?P<name>[^"]*)"$'))
def _when_uuid_for(state: _State, name: str) -> None:
    state.uuid_bytes = uuid_for(name)


@when(parsers.re(r'I call uuid_str_for with the name "(?P<name>[^"]*)"$'))
def _when_uuid_str_for(state: _State, name: str) -> None:
    state.uuid_str = uuid_str_for(name)


@when(parsers.re(r'I call uuid_obj_for with the name "(?P<name>[^"]*)"$'))
def _when_uuid_obj_for(state: _State, name: str) -> None:
    state.uuid_obj = uuid_obj_for(name)


@then(parsers.parse('the 16 returned bytes match the hex "{hex_str}"'))
def _then_bytes_match_hex(state: _State, hex_str: str) -> None:
    assert state.uuid_bytes is not None
    assert len(state.uuid_bytes) == 16
    assert state.uuid_bytes.hex() == hex_str


@then("the bytes, string, and object all encode the same UUID")
def _then_uuid_representations_agree(state: _State) -> None:
    assert state.uuid_bytes is not None
    assert state.uuid_str is not None
    assert state.uuid_obj is not None
    assert state.uuid_obj.bytes == state.uuid_bytes
    assert str(state.uuid_obj) == state.uuid_str


@given(parsers.parse('a 16-byte root derived from the name "{name}"'))
def _given_derived_root(state: _State, name: str) -> None:
    state.derived_root = uuid_for(name)


@when(
    parsers.parse(
        'I call make_cover with domain "{domain}" and correlation_id "{corr_id}"'
    )
)
def _when_make_cover(state: _State, domain: str, corr_id: str) -> None:
    assert state.derived_root is not None
    state.cover = make_cover(domain, state.derived_root, corr_id)


@then(parsers.parse('the cover\'s domain is "{domain}"'))
def _then_cover_domain(state: _State, domain: str) -> None:
    assert state.cover is not None
    assert state.cover.domain == domain


@then(parsers.parse('the cover\'s correlation_id is "{corr_id}"'))
def _then_cover_corr_id(state: _State, corr_id: str) -> None:
    assert state.cover.correlation_id == corr_id


@then("the cover's root bytes match the derived root")
def _then_cover_root_matches(state: _State) -> None:
    assert state.cover.root.value == state.derived_root


@given(parsers.parse('a cover with domain "{domain}" for that root'))
def _given_cover_for_root(state: _State, domain: str) -> None:
    assert state.derived_root is not None
    state.cover = make_cover(domain, state.derived_root, "")


@when("I call make_event_book with that cover, no pages, and no explicit next_sequence")
def _when_make_event_book(state: _State) -> None:
    state.event_book = make_event_book(state.cover, pages=[])


@then(parsers.parse("the resulting event book has next_sequence equal to {seq:d}"))
def _then_event_book_next_sequence(state: _State, seq: int) -> None:
    assert state.event_book.next_sequence == seq


@given(
    parsers.parse(
        'a ScenarioContext with domain "{domain}" and root bytes for "{name}"'
    )
)
def _given_scenario_context(state: _State, domain: str, name: str) -> None:
    ctx = ScenarioContext()
    ctx.domain = domain
    ctx.root = uuid_for(name)
    state.context = ctx


@when("I reset the scenario context")
def _when_reset_context(state: _State) -> None:
    assert state.context is not None
    state.context.reset()


@then("the context's domain is empty")
def _then_context_domain_empty(state: _State) -> None:
    assert state.context.domain == ""


@then("the context's root is empty")
def _then_context_root_empty(state: _State) -> None:
    assert state.context.root == b""


@then("the context's events list is empty")
def _then_context_events_empty(state: _State) -> None:
    assert state.context.events == []
