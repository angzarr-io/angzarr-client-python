"""Step defs for parity/client/destinations.feature, run against the router
binding's Destinations (what saga and process-manager handlers receive).

Pins the canonical query surface across languages: ``has_domain(domain) ->
bool`` and ``domains`` (declaration order).
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from pytest_bdd import given, parsers, scenarios, then

from angzarr_client.router import Destinations

scenarios("parity/client/destinations.feature")


@dataclass
class _State:
    destinations: Destinations | None = None


@pytest.fixture
def state() -> _State:
    return _State()


def _domain_list(spec: str) -> list[str]:
    """Parse `"a"`, `"a" and "b"` or `"a", "b" and "c"` into names, in order."""
    return [part.strip().strip('"') for part in spec.replace(" and ", ", ").split(",")]


@given(
    parsers.re(r"a Destinations for a component declaring output domains? (?P<spec>.+)")
)
def given_destinations(state: _State, spec: str) -> None:
    state.destinations = Destinations(_domain_list(spec))


@then(parsers.re(r'has_domain "(?P<domain>[^"]*)" returns (?P<expected>true|false)'))
def then_has_domain(state: _State, domain: str, expected: str) -> None:
    assert state.destinations is not None
    assert state.destinations.has_domain(domain) is (expected == "true")


@then(parsers.re(r'domains contains "(?P<domain>[^"]+)"'))
def then_domains_contains(state: _State, domain: str) -> None:
    assert state.destinations is not None
    assert domain in state.destinations.domains


@then(parsers.re(r"domains has (?P<count>\d+) entries"))
def then_domains_count(state: _State, count: str) -> None:
    assert state.destinations is not None
    assert len(state.destinations.domains) == int(count)


@then(parsers.re(r"domains in order are (?P<spec>.+)"))
def then_domains_in_order(state: _State, spec: str) -> None:
    assert state.destinations is not None
    expected = [s.strip().strip('"') for s in spec.split(",")]
    assert list(state.destinations.domains) == expected, (
        f"insertion order drift: got {list(state.destinations.domains)}, "
        f"expected {expected}"
    )
