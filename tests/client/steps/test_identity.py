"""Step defs for parity/client/identity.feature: ``compute_root`` is
uuid5(NAMESPACE_OID, domain + ":" + key), the client's only identity
derivation, and ``to_proto_bytes`` is the UUID's 16 bytes."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

import pytest
from pytest_bdd import parsers, scenarios, then, when

import angzarr_client as ac

scenarios("parity/client/identity.feature")


@dataclass
class IdentityWorld:
    uuids: list[UUID] = field(default_factory=list)
    bytes_: bytes | None = None


@pytest.fixture
def identity_world() -> IdentityWorld:
    return IdentityWorld()


@when(parsers.parse('I call compute_root with domain "{domain}" and key "{key}"'))
@when(
    parsers.parse(
        'I call compute_root with domain "{domain}" and key "{key}" a second time'
    )
)
def _when_compute_root(identity_world: IdentityWorld, domain: str, key: str) -> None:
    identity_world.uuids.append(ac.compute_root(domain, key))


@when(parsers.parse('I call compute_root with domain "{domain}" and key ""'))
def _when_compute_root_empty_key(identity_world: IdentityWorld, domain: str) -> None:
    identity_world.uuids.append(ac.compute_root(domain, ""))


@when("I pass the resulting UUID through to_proto_bytes")
def _when_to_proto_bytes(identity_world: IdentityWorld) -> None:
    identity_world.bytes_ = ac.to_proto_bytes(identity_world.uuids[-1])


@then("both calls return the same UUID")
def _then_same(identity_world: IdentityWorld) -> None:
    assert len(identity_world.uuids) == 2
    assert identity_world.uuids[0] == identity_world.uuids[1]


@then("the two UUIDs differ")
def _then_differ(identity_world: IdentityWorld) -> None:
    assert len(identity_world.uuids) == 2
    assert identity_world.uuids[0] != identity_world.uuids[1]


@then(parsers.parse('the resulting UUID equals "{expected}"'))
def _then_equals(identity_world: IdentityWorld, expected: str) -> None:
    assert str(identity_world.uuids[-1]) == expected


@then(parsers.parse('the first UUID equals "{expected}"'))
def _then_first_equals(identity_world: IdentityWorld, expected: str) -> None:
    assert str(identity_world.uuids[0]) == expected


@then(parsers.parse('the second UUID equals "{expected}"'))
def _then_second_equals(identity_world: IdentityWorld, expected: str) -> None:
    assert str(identity_world.uuids[1]) == expected


@then("the byte length is 16")
def _then_len(identity_world: IdentityWorld) -> None:
    assert identity_world.bytes_ is not None and len(identity_world.bytes_) == 16


@then(parsers.parse('the bytes match the hex "{hex_str}"'))
def _then_hex(identity_world: IdentityWorld, hex_str: str) -> None:
    assert identity_world.bytes_ == bytes.fromhex(hex_str)
