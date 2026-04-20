"""Tests for angzarr_client.compensation helpers."""

from uuid import UUID as PyUUID

from angzarr_client.compensation import (
    CompensationContext,
    PMRevocationResponse,
    RejectionHandlerResponse,
    delegate_to_framework,
    emit_compensation_events,
    pm_delegate_to_framework,
    pm_emit_compensation_events,
)
from angzarr_client.helpers import uuid_to_proto
from angzarr_client.proto.angzarr import command_handler_pb2 as command_handler
from angzarr_client.proto.angzarr import types_pb2 as types


def _make_rejection_notification(
    rejected_command: types.CommandBook | None = None,
    reason: str = "bad state",
) -> types.Notification:
    rej = types.RejectionNotification(rejection_reason=reason)
    if rejected_command is not None:
        rej.rejected_command.CopyFrom(rejected_command)
    notif = types.Notification()
    notif.payload.Pack(rej)
    return notif


class TestCompensationContextFromNotification:
    def test_empty_notification_yields_defaults(self) -> None:
        ctx = CompensationContext.from_notification(types.Notification())
        assert ctx.source_event_sequence == 0
        assert ctx.rejection_reason == ""
        assert ctx.rejected_command is None
        assert ctx.source_aggregate is None

    def test_rejection_reason_extracted(self) -> None:
        notif = _make_rejection_notification(reason="out of stock")
        ctx = CompensationContext.from_notification(notif)
        assert ctx.rejection_reason == "out of stock"

    def test_rejected_command_extracted(self) -> None:
        cmd = types.CommandBook()
        cmd.cover.domain = "inventory"
        notif = _make_rejection_notification(rejected_command=cmd)
        ctx = CompensationContext.from_notification(notif)
        assert ctx.rejected_command is not None
        assert ctx.rejected_command.cover.domain == "inventory"

    def test_source_from_angzarr_deferred(self) -> None:
        cmd = types.CommandBook()
        cmd.cover.domain = "inventory"
        page = cmd.pages.add()
        page.header.sequence = 1
        deferred = page.header.angzarr_deferred
        deferred.source_seq = 42
        deferred.source.domain = "orders"
        uid = PyUUID("deadbeef-dead-beef-dead-beefdeadbeef")
        deferred.source.root.CopyFrom(uuid_to_proto(uid))
        notif = _make_rejection_notification(rejected_command=cmd)
        ctx = CompensationContext.from_notification(notif)
        assert ctx.source_event_sequence == 42
        assert ctx.source_aggregate is not None
        assert ctx.source_domain == "orders"
        assert ctx.source_root == uid.bytes

    def test_no_deferred_header_yields_no_source(self) -> None:
        cmd = types.CommandBook()
        page = cmd.pages.add()
        page.header.sequence = 1
        notif = _make_rejection_notification(rejected_command=cmd)
        ctx = CompensationContext.from_notification(notif)
        assert ctx.source_aggregate is None
        assert ctx.source_event_sequence == 0

    def test_deferred_without_source_subfield(self) -> None:
        cmd = types.CommandBook()
        page = cmd.pages.add()
        page.header.sequence = 1
        page.header.angzarr_deferred.source_seq = 7
        notif = _make_rejection_notification(rejected_command=cmd)
        ctx = CompensationContext.from_notification(notif)
        assert ctx.source_event_sequence == 7
        assert ctx.source_aggregate is None


class TestCompensationContextProperties:
    def test_rejected_command_type(self) -> None:
        cmd = types.CommandBook()
        page = cmd.pages.add()
        page.header.sequence = 1
        page.command.Pack(types.Cover(domain="x"))
        ctx = CompensationContext.from_notification(
            _make_rejection_notification(rejected_command=cmd)
        )
        assert ctx.rejected_command_type is not None
        assert ctx.rejected_command_type.endswith("angzarr_client.proto.angzarr.Cover")

    def test_rejected_command_type_none_when_no_command_in_page(self) -> None:
        cmd = types.CommandBook()
        page = cmd.pages.add()
        page.header.sequence = 1
        ctx = CompensationContext.from_notification(
            _make_rejection_notification(rejected_command=cmd)
        )
        assert ctx.rejected_command_type is None

    def test_rejected_command_type_none_when_no_rejected_command(self) -> None:
        ctx = CompensationContext.from_notification(types.Notification())
        assert ctx.rejected_command_type is None

    def test_source_domain_none_without_source(self) -> None:
        ctx = CompensationContext.from_notification(types.Notification())
        assert ctx.source_domain is None

    def test_source_root_none_without_source(self) -> None:
        ctx = CompensationContext.from_notification(types.Notification())
        assert ctx.source_root is None

    def test_source_root_none_when_source_has_no_root(self) -> None:
        cmd = types.CommandBook()
        page = cmd.pages.add()
        page.header.sequence = 1
        page.header.angzarr_deferred.source.domain = "orders"
        ctx = CompensationContext.from_notification(
            _make_rejection_notification(rejected_command=cmd)
        )
        assert ctx.source_root is None

    def test_dispatch_key_empty_without_rejected_command(self) -> None:
        ctx = CompensationContext.from_notification(types.Notification())
        assert ctx.dispatch_key == ""

    def test_dispatch_key_empty_when_no_cover(self) -> None:
        cmd = types.CommandBook()
        page = cmd.pages.add()
        page.header.sequence = 1
        page.command.Pack(types.Cover(domain="x"))
        ctx = CompensationContext.from_notification(
            _make_rejection_notification(rejected_command=cmd)
        )
        assert ctx.dispatch_key == ""

    def test_dispatch_key_empty_when_no_command_type(self) -> None:
        cmd = types.CommandBook()
        cmd.cover.domain = "inventory"
        page = cmd.pages.add()
        page.header.sequence = 1  # no command packed
        ctx = CompensationContext.from_notification(
            _make_rejection_notification(rejected_command=cmd)
        )
        assert ctx.dispatch_key == ""

    def test_dispatch_key_domain_slash_command(self) -> None:
        cmd = types.CommandBook()
        cmd.cover.domain = "inventory"
        page = cmd.pages.add()
        page.header.sequence = 1
        page.command.Pack(types.Cover())
        ctx = CompensationContext.from_notification(
            _make_rejection_notification(rejected_command=cmd)
        )
        assert ctx.dispatch_key == "inventory/angzarr.Cover"


class TestAggregateHelpers:
    def test_delegate_to_framework_defaults(self) -> None:
        resp = delegate_to_framework(reason="no compensation logic")
        assert isinstance(resp, command_handler.BusinessResponse)
        assert resp.revocation.emit_system_revocation is True
        assert resp.revocation.send_to_dead_letter_queue is False
        assert resp.revocation.escalate is False
        assert resp.revocation.abort is False
        assert resp.revocation.reason == "no compensation logic"

    def test_delegate_to_framework_all_flags(self) -> None:
        resp = delegate_to_framework(
            reason="escalate!",
            emit_system_event=False,
            send_to_dead_letter=True,
            escalate=True,
            abort=True,
        )
        assert resp.revocation.emit_system_revocation is False
        assert resp.revocation.send_to_dead_letter_queue is True
        assert resp.revocation.escalate is True
        assert resp.revocation.abort is True

    def test_emit_compensation_events_returns_events(self) -> None:
        book = types.EventBook()
        book.cover.domain = "orders"
        resp = emit_compensation_events(book)
        assert isinstance(resp, command_handler.BusinessResponse)
        assert resp.events.cover.domain == "orders"


class TestProcessManagerHelpers:
    def test_pm_delegate_default(self) -> None:
        resp = pm_delegate_to_framework(reason="no pm logic")
        assert isinstance(resp, PMRevocationResponse)
        assert resp.process_events is None
        assert resp.revocation is not None
        assert resp.revocation.emit_system_revocation is True
        assert resp.revocation.reason == "no pm logic"

    def test_pm_delegate_no_system_event(self) -> None:
        resp = pm_delegate_to_framework(reason="silent", emit_system_event=False)
        assert resp.revocation is not None
        assert resp.revocation.emit_system_revocation is False

    def test_pm_emit_compensation_events_minimal(self) -> None:
        book = types.EventBook()
        book.cover.domain = "fulfillment"
        resp = pm_emit_compensation_events(book)
        assert resp.process_events is book
        assert resp.revocation is not None
        assert resp.revocation.emit_system_revocation is False
        assert resp.revocation.reason == ""

    def test_pm_emit_compensation_events_with_system_event(self) -> None:
        book = types.EventBook()
        resp = pm_emit_compensation_events(
            book, also_emit_system_event=True, reason="because"
        )
        assert resp.revocation is not None
        assert resp.revocation.emit_system_revocation is True
        assert resp.revocation.reason == "because"


class TestResponseDataclasses:
    def test_rejection_handler_response_defaults(self) -> None:
        r = RejectionHandlerResponse()
        assert r.events is None
        assert r.notification is None

    def test_pm_revocation_response_defaults(self) -> None:
        r = PMRevocationResponse()
        assert r.process_events is None
        assert r.revocation is None
