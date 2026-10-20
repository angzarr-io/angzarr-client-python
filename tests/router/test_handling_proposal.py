"""Item-4 proposal, handler side (docs/design/handling-and-command-modes.md):
the conflict handling and rules-version surface the spec requires of
aggregate authors, written as tests before the API exists.

Every test here is a strict xfail until the owner signs the proposal off:
each fails today because the proposed symbol is missing, and an XPASS fails
the run, so implementing a symbol forces its marker off. Symbols mirror the
spec's names (types.proto Handling / ConflictResolution, decision 12.7's
``resolve_conflicts(window, nonce)``, options.proto's ``changed``) and the
router core's Rust API (``CommandContext.{expected_sequence,
conflict_resolution, conflict}``, ``aggregate::Conflict``,
``with_commutative(changed)``).

The COMMUTATIVE fixture uses well-known types so it needs no generated code:
the state is a ``google.protobuf.Struct`` of item quantities (one conflict
unit per item, as handling.feature's order aggregate), ItemAdded is a
``StringValue`` naming the item, and AddItem a ``Value`` naming it.
"""

from __future__ import annotations

import pytest
from google.protobuf import struct_pb2, wrappers_pb2

from angzarr_client.proto.io.angzarr.v1 import types_pb2
from angzarr_client.router import (
    AggregateDispatch,
    CodedError,
    GrpcCode,
    Rebuilder,
    Router,
    pack,
)

PENDING = pytest.mark.xfail(
    strict=True,
    reason="item-4 proposal (handler conflict API) pending owner sign-off",
)

_ADD_ITEM = "google.protobuf.Value"
_ITEM_ADDED = "google.protobuf.StringValue"
_STRATEGY = types_pb2.ConflictResolution


def _item_added(state: struct_pb2.Struct, event) -> None:
    item = wrappers_pb2.StringValue.FromString(event.value).value
    current = state.fields[item].number_value if item in state.fields else 0
    state.fields[item].number_value = current + 1


def _add_item(cmd, _state, _cctx):
    item = struct_pb2.Value.FromString(cmd.value).string_value
    book = types_pb2.EventBook()
    book.pages.add().payload.CopyFrom(pack(wrappers_pb2.StringValue(value=item)))
    return book


def _changed(before: struct_pb2.Struct, after: struct_pb2.Struct) -> list[str]:
    """The generated changed() for the item-quantity state: one unit per
    item whose quantity differs."""
    items = set(before.fields) | set(after.fields)
    return sorted(
        f"items[{item}]"
        for item in items
        if before.fields.get(item) != after.fields.get(item)
    )


def _order(handle=_add_item) -> AggregateDispatch:
    return AggregateDispatch(
        "Order",
        "order",
        Rebuilder(factory=struct_pb2.Struct).apply(_ITEM_ADDED, _item_added),
    ).on_command(_ADD_ITEM, handle)


def _command(
    item: str,
    window: list[str],
    *,
    resolution: int,
    expected: int,
    nonce: bytes = b"n-1",
):
    """AddItem ``item`` over prior ItemAdded events of ``window`` (at
    sequences 0..), handled under ``resolution`` at ``expected``."""
    cc = types_pb2.ContextualCommand()
    cc.command.cover.domain = "order"
    cc.command.page.command.payload.CopyFrom(pack(struct_pb2.Value(string_value=item)))
    cc.command.handling.expected_sequence = expected
    cc.command.handling.conflict_resolution = resolution
    cc.conflict_nonce = nonce
    for seq, prior in enumerate(window):
        page = cc.events.pages.add(sequence=seq)
        page.payload.CopyFrom(pack(wrappers_pb2.StringValue(value=prior)))
    cc.events.next_sequence = len(window)
    return cc


def _dispatch(dispatch: AggregateDispatch, cc):
    with Router() as router:
        router.register_aggregate(dispatch)
        return router.dispatch(cc)


# --- the resolved Handling reaches business code ---


@PENDING
def test_c0541_under_strict_business_code_sees_the_handling_and_no_conflict():
    seen = []

    def handle(cmd, state, cctx):
        seen.append((cctx.expected_sequence, cctx.conflict_resolution, cctx.conflict))
        return _add_item(cmd, state, cctx)

    cc = _command(
        "apple",
        ["pear", "plum"],
        resolution=_STRATEGY.CONFLICT_RESOLUTION_STRICT,
        expected=2,
    )
    _dispatch(_order(handle), cc)

    assert seen == [(2, _STRATEGY.CONFLICT_RESOLUTION_STRICT, None)]


# --- AGGREGATE_HANDLES: resolve_conflicts(window, nonce) ---


@PENDING
def test_c0542_settling_the_window_with_the_delivered_nonce_is_accepted():
    seen = []

    def handle(cmd, state, cctx):
        conflict = cctx.conflict
        seen.append([page.sequence for page in conflict.window])
        conflict.resolve_conflicts(conflict.window, conflict.nonce)
        return _add_item(cmd, state, cctx)

    cc = _command(
        "apple",
        ["pear", "plum", "fig"],
        resolution=_STRATEGY.CONFLICT_RESOLUTION_AGGREGATE_HANDLES,
        expected=1,
    )
    resp = _dispatch(_order(handle), cc)

    assert seen == [[1, 2]]
    assert len(resp.events.pages) == 1


@PENDING
def test_c0544_settling_with_a_made_up_nonce_fails_as_stale():
    def handle(cmd, state, cctx):
        cctx.conflict.resolve_conflicts(cctx.conflict.window, b"made-up")
        return _add_item(cmd, state, cctx)

    cc = _command(
        "apple",
        ["pear"],
        resolution=_STRATEGY.CONFLICT_RESOLUTION_AGGREGATE_HANDLES,
        expected=0,
    )
    with pytest.raises(CodedError) as exc:
        _dispatch(_order(handle), cc)

    assert (exc.value.code, exc.value.grpc) == ("CONFLICT_UNRESOLVED", GrpcCode.ABORTED)


@PENDING
def test_c0545_an_empty_window_still_has_to_be_settled():
    seen = []

    def handle(cmd, state, cctx):
        seen.append(list(cctx.conflict.window))
        cctx.conflict.resolve_conflicts(cctx.conflict.window, cctx.conflict.nonce)
        return _add_item(cmd, state, cctx)

    cc = _command(
        "apple",
        ["pear"],
        resolution=_STRATEGY.CONFLICT_RESOLUTION_AGGREGATE_HANDLES,
        expected=1,
    )
    _dispatch(_order(handle), cc)

    assert seen == [[]]


# --- COMMUTATIVE: with_commutative(changed) ---


@PENDING
def test_c0546_a_stale_command_changing_other_units_than_the_window_is_kept():
    seen = []

    def handle(cmd, state, cctx):
        seen.append(cctx.conflict)
        return _add_item(cmd, state, cctx)

    cc = _command(
        "apple",
        ["pear", "pear"],
        resolution=_STRATEGY.CONFLICT_RESOLUTION_COMMUTATIVE,
        expected=0,
    )
    resp = _dispatch(_order(handle).with_commutative(_changed), cc)

    assert len(resp.events.pages) == 1
    assert seen == [None]


@PENDING
def test_c0547_a_stale_command_changing_a_unit_the_window_changed_fails_as_stale():
    cc = _command(
        "pear",
        ["apple", "pear"],
        resolution=_STRATEGY.CONFLICT_RESOLUTION_COMMUTATIVE,
        expected=1,
    )
    with pytest.raises(CodedError) as exc:
        _dispatch(_order().with_commutative(_changed), cc)

    assert (exc.value.code, exc.value.grpc) == ("CONFLICT_OVERLAP", GrpcCode.ABORTED)


@PENDING
def test_c0549_business_code_decides_on_the_head_window_included():
    seen = []

    def handle(cmd, state, cctx):
        seen.append(sorted(state.fields))
        return _add_item(cmd, state, cctx)

    cc = _command(
        "apple",
        ["pear", "plum"],
        resolution=_STRATEGY.CONFLICT_RESOLUTION_COMMUTATIVE,
        expected=0,
    )
    _dispatch(_order(handle).with_commutative(_changed), cc)

    assert seen == [["pear", "plum"]]


@PENDING
def test_commutative_is_announced_in_the_registration_hello():
    with Router() as router:
        router.register_aggregate(
            _order().with_commutative(_changed).with_rules_version("1.4.2")
        )
        (hello,) = router.component_descriptors()

    assert list(hello.state.options.conflict_resolutions) == [
        _STRATEGY.CONFLICT_RESOLUTION_COMMUTATIVE,
        _STRATEGY.CONFLICT_RESOLUTION_STRICT,
        _STRATEGY.CONFLICT_RESOLUTION_AGGREGATE_HANDLES,
    ]
