"""Unit tests for angzarr_client.identity."""

import uuid

import angzarr_client as ac
from angzarr_client import compute_root, to_proto_bytes


def test_compute_root_hashes_domain_colon_key_under_the_oid_namespace():
    assert compute_root("cart", "alice") == uuid.uuid5(uuid.NAMESPACE_OID, "cart:alice")


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
