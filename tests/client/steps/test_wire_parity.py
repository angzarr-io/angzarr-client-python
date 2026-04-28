"""Step defs for features/client/wire_parity.feature.

Cross-language wire-format parity check — verifies that the Python client
produces byte-identical proto-encoded output as the Rust client for the
same logical input. The Rust sibling at
``client-rust/main/tests/steps/wire_parity.rs`` exercises the same
scenarios against the same expected SHA-256 values.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Optional

import pytest
from google.protobuf.any_pb2 import Any as ProtoAny
from pytest_bdd import given, parsers, scenarios, then, when

from angzarr_client.destinations import Destinations
from angzarr_client.proto.angzarr import types_pb2 as types

scenarios("wire_parity.feature")


@dataclass
class _State:
    book: Optional[types.CommandBook] = None
    sequences: dict[str, int] = field(default_factory=dict)


@pytest.fixture
def state() -> _State:
    return _State()


def _parse_hex_range(s: str) -> bytes:
    """Parse `00..0f` (inclusive) into bytes [0x00, ..., 0x0f]."""
    lo_str, hi_str = s.split("..")
    lo, hi = int(lo_str, 16), int(hi_str, 16)
    return bytes(range(lo, hi + 1))


def _parse_hex_bytes(s: str) -> bytes:
    """Parse `01020304` into bytes b'\\x01\\x02\\x03\\x04'."""
    return bytes.fromhex(s)


@given(
    parsers.re(
        r'a CommandBook with cover\.domain "(?P<domain>[^"]+)" '
        r"and cover\.root bytes (?P<root_hex>\S+) "
        r'and correlation_id "(?P<correlation_id>[^"]+)"'
    )
)
def given_commandbook(
    state: _State, domain: str, root_hex: str, correlation_id: str
) -> None:
    book = types.CommandBook()
    book.cover.domain = domain
    book.cover.root.value = _parse_hex_range(root_hex)
    book.cover.correlation_id = correlation_id
    state.book = book


@given(
    parsers.re(
        r'a single CommandPage with command type_url "(?P<type_url>[^"]+)" '
        r"and payload bytes (?P<payload_hex>\S+)"
    )
)
def given_single_page(state: _State, type_url: str, payload_hex: str) -> None:
    assert state.book is not None, "CommandBook must be set first"
    payload = ProtoAny()
    payload.type_url = type_url
    payload.value = _parse_hex_bytes(payload_hex)
    page = state.book.pages.add()
    page.command.CopyFrom(payload)


@given(parsers.re(r'destination_sequences mapping "(?P<domain>[^"]+)" to (?P<seq>\d+)'))
def given_sequences(state: _State, domain: str, seq: str) -> None:
    state.sequences[domain] = int(seq)


@when(parsers.re(r'I stamp the command for domain "(?P<domain>[^"]+)"'))
def when_stamp(state: _State, domain: str) -> None:
    assert state.book is not None
    Destinations(state.sequences).stamp_command(state.book, domain)


@then(
    parsers.re(
        r'the deterministically-encoded CommandBook hashes to SHA-256 "(?P<expected>[0-9a-f]{64})"'
    )
)
def then_hash(state: _State, expected: str) -> None:
    assert state.book is not None
    raw = state.book.SerializeToString(deterministic=True)
    actual = hashlib.sha256(raw).hexdigest()
    assert actual == expected, (
        "Wire-format drift from expected golden.\n"
        f"  actual:   {actual}\n  expected: {expected}"
    )
