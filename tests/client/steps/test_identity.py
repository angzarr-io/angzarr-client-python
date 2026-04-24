"""Step defs for features/client/identity.feature.

Exercises the cross-language deterministic identity contract: compute_root
under the RFC 4122 OID namespace with an "angzarr" prefix, the per-domain
helpers that route through it, and inventory_product_root which hashes
under the DNS namespace directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from pytest_bdd import given, parsers, scenarios, then, when

import angzarr_client as ac

scenarios("identity.feature")


@dataclass
class IdentityWorld:
    """Captures UUIDs produced by step invocations for cross-step assertions."""

    uuids: list[UUID] = field(default_factory=list)
    bytes_: bytes | None = None


def _world(request) -> IdentityWorld:
    return request.getfixturevalue("identity_world")


# Pytest-bdd fixture via conftest.py — but this feature has self-contained
# state, so we register the fixture inline below.

import pytest  # noqa: E402


@pytest.fixture
def identity_world() -> IdentityWorld:
    return IdentityWorld()


@given("the angzarr client library is importable at its public root")
def _given_importable() -> None:
    # Inherited phrasing from parity.feature; no-op for identity.
    pass


@when(
    parsers.re(
        r'I call compute_root with domain "(?P<domain>[^"]*)" and key "(?P<key>[^"]*)"$'
    )
)
def _when_compute_root(identity_world: IdentityWorld, domain: str, key: str) -> None:
    identity_world.uuids.append(ac.compute_root(domain, key))


@when(
    parsers.re(
        r'I call compute_root with domain "(?P<domain>[^"]*)" and key "(?P<key>[^"]*)" a second time$'
    )
)
def _when_compute_root_again(
    identity_world: IdentityWorld, domain: str, key: str
) -> None:
    identity_world.uuids.append(ac.compute_root(domain, key))


@when(parsers.parse('I call "{helper}" with "{input}"'))
def _when_call_helper(identity_world: IdentityWorld, helper: str, input: str) -> None:
    fn = getattr(ac, helper)
    identity_world.uuids.append(fn(input))


@when(parsers.parse('I call inventory_product_root with "{product_id}"'))
def _when_inventory_product_root(
    identity_world: IdentityWorld, product_id: str
) -> None:
    identity_world.uuids.append(ac.inventory_product_root(product_id))


@when(parsers.parse('I call customer_root with "{email}"'))
def _when_customer_root(identity_world: IdentityWorld, email: str) -> None:
    identity_world.uuids.append(ac.customer_root(email))


@when("I pass the resulting UUID through to_proto_bytes")
def _when_to_proto_bytes(identity_world: IdentityWorld) -> None:
    identity_world.bytes_ = ac.to_proto_bytes(identity_world.uuids[-1])


@then("both calls return the same UUID")
def _then_same_uuid(identity_world: IdentityWorld) -> None:
    assert len(identity_world.uuids) >= 2
    assert identity_world.uuids[0] == identity_world.uuids[1]


@then("the two UUIDs differ")
def _then_different_uuids(identity_world: IdentityWorld) -> None:
    assert len(identity_world.uuids) >= 2
    assert identity_world.uuids[0] != identity_world.uuids[1]


@then(parsers.parse('the resulting UUID equals "{expected}"'))
def _then_uuid_equals(identity_world: IdentityWorld, expected: str) -> None:
    assert identity_world.uuids
    actual = identity_world.uuids[-1]
    assert str(actual) == expected, f"expected {expected}, got {actual}"


@then(
    parsers.parse(
        'INVENTORY_PRODUCT_NAMESPACE equals the UUID "{expected}"'
    )
)
def _then_namespace_equals(expected: str) -> None:
    assert str(ac.INVENTORY_PRODUCT_NAMESPACE) == expected


@then("the byte length is 16")
def _then_byte_length_16(identity_world: IdentityWorld) -> None:
    assert identity_world.bytes_ is not None
    assert len(identity_world.bytes_) == 16


@then(parsers.parse('the bytes match the hex "{hex_str}"'))
def _then_bytes_match_hex(identity_world: IdentityWorld, hex_str: str) -> None:
    assert identity_world.bytes_ is not None
    assert identity_world.bytes_.hex() == hex_str
