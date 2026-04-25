"""Cross-language wire-format parity for Destinations.stamp_command.

Locks in a SHA-256 of the deterministically-serialized stamped CommandBook
for a fixed input. The Rust client has a sibling unit test asserting the
same hash; if either side changes how `stamp_command` modifies wire bytes,
both tests must agree on the new hash. Drift will fail the test on at
least one side.

Determinism:
  - Fixed UUID, fixed domain, fixed correlation_id, fixed type_url, fixed payload.
  - No timestamps, no random fields.
  - SerializeToString(deterministic=True) — protobuf canonical order.
"""

from __future__ import annotations

import hashlib

from google.protobuf.any_pb2 import Any as ProtoAny

from angzarr_client.destinations import Destinations
from angzarr_client.proto.angzarr import types_pb2 as types

# Fixed input. Changing ANY of these constants invalidates the golden hash.
ROOT_BYTES = bytes(range(16))  # UUID 00010203-...-0e0f
DOMAIN = "saga-x"
CORRELATION_ID = "corr-1"
COMMAND_TYPE_URL = "type.googleapis.com/example.Foo"
COMMAND_PAYLOAD = b"\x01\x02\x03\x04"
TARGET_DOMAIN = "inventory"
TARGET_SEQUENCE = 5

# Golden hash — SHA-256 of the stamped CommandBook's deterministic serialization.
# Rust's tests/wire_parity.rs (or src/router/state.rs unit test) MUST assert
# this same value for the same input. If both sides agree, wire parity holds.
GOLDEN_SHA256 = "8a6da2dfa422553d73fcd840f6ad501c91ac6ffcac2f591183146ab6c042ace9"


def _build_unstamped_book() -> types.CommandBook:
    """Construct the canonical input CommandBook used by the parity test."""
    book = types.CommandBook()
    book.cover.domain = DOMAIN
    book.cover.root.value = ROOT_BYTES
    book.cover.correlation_id = CORRELATION_ID

    page = book.pages.add()
    payload = ProtoAny()
    payload.type_url = COMMAND_TYPE_URL
    payload.value = COMMAND_PAYLOAD
    page.command.CopyFrom(payload)
    return book


def test_stamp_command_wire_parity():
    book = _build_unstamped_book()
    Destinations({TARGET_DOMAIN: TARGET_SEQUENCE}).stamp_command(book, TARGET_DOMAIN)

    raw = book.SerializeToString(deterministic=True)
    digest = hashlib.sha256(raw).hexdigest()

    assert digest == GOLDEN_SHA256, (
        f"Stamped CommandBook wire bytes drifted.\n"
        f"  expected: {GOLDEN_SHA256}\n"
        f"  actual:   {digest}\n"
        f"If this is intentional, update the golden in BOTH this test and "
        f"the Rust sibling test in tandem."
    )
