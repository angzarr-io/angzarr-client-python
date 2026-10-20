"""Unit tests for angzarr_client.identity."""

import uuid

import pytest

import angzarr_client as ac
from angzarr_client import InvalidArgumentError, compute_root, to_proto_bytes

# types.proto, region derived_ids (decision 9.2).
ANGZARR_ROOT_NAMESPACE = uuid.UUID("d4741b1a-52cd-43cf-add1-3dbb9615510b")


def test_compute_root_hashes_domain_colon_key_under_the_root_namespace():
    assert compute_root("cart", "alice") == uuid.uuid5(
        ANGZARR_ROOT_NAMESPACE, "cart:alice"
    )


def test_compute_root_matches_the_spec_example():
    # types.proto: compute_root("player", "alice@example.com").
    assert str(compute_root("player", "alice@example.com")) == (
        "cfe936c0-2912-5ca9-96e6-f37c0b441759"
    )


def test_compute_root_refuses_a_domain_containing_a_colon():
    # C-0513.
    with pytest.raises(InvalidArgumentError) as exc:
        compute_root("bad:domain", "k")
    assert exc.value.code == "DOMAIN_CONTAINS_COLON"
    assert exc.value.details == {"domain": "bad:domain"}


def test_compute_root_accepts_a_colon_in_the_key():
    assert compute_root("cart", "a:b") == uuid.uuid5(ANGZARR_ROOT_NAMESPACE, "cart:a:b")


def test_compute_root_is_uuid_version_5():
    assert compute_root("order", "ord-42").version == 5


def test_compute_root_separator_distinguishes_shifted_boundaries():
    assert compute_root("ab", "c") != compute_root("a", "bc")


def test_to_proto_bytes_is_the_uuid_bytes():
    root = compute_root("customer", "alice@x.com")
    assert to_proto_bytes(root) == root.bytes


def test_compute_root_is_the_only_identity_helper():
    exported = {n for n in ac.__all__ if n.endswith("_root")}
    assert exported == {"compute_root"}
