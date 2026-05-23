"""Step defs for features/client/destinations.feature.

Pins the canonical query surface on Destinations across languages:
``has_domain(domain) -> bool`` and ``domains -> list[str]``. The Rust
sibling at ``client-rust/main/tests/steps/destinations.rs`` exercises
the same scenarios against the same canonical names.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pytest
from pytest_bdd import given, parsers, scenarios, then

from angzarr_client.destinations import Destinations

scenarios("../../parity/client/destinations.feature")


@dataclass
class _State:
    destinations: Optional[Destinations] = None


@pytest.fixture
def state() -> _State:
    return _State()


def _parse_sequence_map(spec: str) -> dict[str, int]:
    """Parse `"a" to 1 and "b" to 2 and ...` into a {name: seq} dict."""
    parts = spec.split(" and ")
    out: dict[str, int] = {}
    for part in parts:
        name_str, seq_str = part.split(" to ")
        out[name_str.strip().strip('"')] = int(seq_str)
    return out


@given(parsers.re(r"a Destinations built from sequences mapping (?P<spec>.+)"))
def given_destinations(state: _State, spec: str) -> None:
    state.destinations = Destinations(_parse_sequence_map(spec))


@given(parsers.re(r"a Destinations built from an ordered sequence list (?P<spec>.+)"))
def given_destinations_ordered(state: _State, spec: str) -> None:
    """Build from an explicitly-ordered list like `"zulu" then "alpha" then "mike"`.

    Uses an insertion-order-preserving dict so the iteration order at
    `Destinations.domains` matches the spec literally. Each named
    destination gets sequence 0; the order is what's under test.
    """
    names = [s.strip().strip('"') for s in spec.split(" then ")]
    ordered: dict[str, int] = {}
    for name in names:
        ordered[name] = 0
    state.destinations = Destinations(ordered)


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
