"""Shared helpers for building proto messages and dispatch inputs."""

from __future__ import annotations

from google.protobuf.any_pb2 import Any as ProtoAny

from angzarr_client.helpers import TYPE_URL_PREFIX
from angzarr_client.proto.angzarr import (
    CommandBook,
    CommandPage,
    ContextualCommand,
    Cover,
    EventBook,
    EventPage,
    PageHeader,
    SagaHandleRequest,
)
from angzarr_client.proto.angzarr import process_manager_pb2 as pm_pb
from angzarr_client.proto.angzarr import types_pb2 as types_pb


def pack_event_page(msg, seq: int) -> EventPage:
    any_evt = ProtoAny()
    any_evt.type_url = TYPE_URL_PREFIX + msg.DESCRIPTOR.full_name
    any_evt.value = msg.SerializeToString()
    page = EventPage()
    page.header.CopyFrom(PageHeader(sequence=seq))
    page.event.CopyFrom(any_evt)
    return page


def event_book(msgs: list, domain: str) -> EventBook:
    book = EventBook()
    book.cover.CopyFrom(Cover(domain=domain))
    for i, m in enumerate(msgs):
        book.pages.append(pack_event_page(m, seq=i))
    book.next_sequence = len(msgs)
    return book


def contextual_command(
    cmd, domain: str, prior: EventBook | None = None
) -> ContextualCommand:
    any_cmd = ProtoAny()
    any_cmd.type_url = TYPE_URL_PREFIX + cmd.DESCRIPTOR.full_name
    any_cmd.value = cmd.SerializeToString()
    page = CommandPage()
    page.header.CopyFrom(PageHeader(sequence=0))
    page.command.CopyFrom(any_cmd)
    book = CommandBook()
    book.cover.CopyFrom(Cover(domain=domain))
    book.pages.append(page)
    req = ContextualCommand()
    req.command.CopyFrom(book)
    if prior is not None:
        req.events.CopyFrom(prior)
    return req


def command_book(cmd_msg, target_domain: str, seq: int = 0) -> CommandBook:
    any_cmd = ProtoAny()
    any_cmd.type_url = TYPE_URL_PREFIX + cmd_msg.DESCRIPTOR.full_name
    any_cmd.value = cmd_msg.SerializeToString()
    page = CommandPage()
    page.header.CopyFrom(PageHeader(sequence=seq))
    page.command.CopyFrom(any_cmd)
    book = CommandBook()
    book.cover.CopyFrom(Cover(domain=target_domain))
    book.pages.append(page)
    return book


def saga_request(
    event_msgs: list, source_domain: str, dest_seqs: dict | None = None
) -> SagaHandleRequest:
    req = SagaHandleRequest()
    req.source.CopyFrom(event_book(event_msgs, domain=source_domain))
    for k, v in (dest_seqs or {}).items():
        req.destination_sequences[k] = v
    return req


def pm_request(
    trigger_msgs: list,
    source_domain: str,
    process_state_msgs: list | None = None,
    pm_domain: str = "fulfillment",
    dest_seqs: dict | None = None,
) -> pm_pb.ProcessManagerHandleRequest:
    req = pm_pb.ProcessManagerHandleRequest()
    req.trigger.CopyFrom(event_book(trigger_msgs, domain=source_domain))
    req.process_state.CopyFrom(event_book(process_state_msgs or [], domain=pm_domain))
    for k, v in (dest_seqs or {}).items():
        req.destination_sequences[k] = v
    return req


def notification_for(rejected_cmd_msg, target_domain: str) -> types_pb.Notification:
    """Build a Notification whose RejectionNotification targets the given cmd."""
    cb = command_book(rejected_cmd_msg, target_domain=target_domain)
    rej = types_pb.RejectionNotification()
    rej.rejected_command.CopyFrom(cb)
    rej.rejection_reason = "test rejection"

    rej_any = ProtoAny()
    rej_any.type_url = TYPE_URL_PREFIX + rej.DESCRIPTOR.full_name
    rej_any.value = rej.SerializeToString()

    n = types_pb.Notification()
    n.payload.CopyFrom(rej_any)
    return n


def contextual_notification(
    notification: types_pb.Notification, aggregate_domain: str
) -> ContextualCommand:
    n_any = ProtoAny()
    n_any.type_url = TYPE_URL_PREFIX + notification.DESCRIPTOR.full_name
    n_any.value = notification.SerializeToString()
    page = CommandPage()
    page.header.CopyFrom(PageHeader(sequence=0))
    page.command.CopyFrom(n_any)
    book = CommandBook()
    book.cover.CopyFrom(Cover(domain=aggregate_domain))
    book.pages.append(page)
    req = ContextualCommand()
    req.command.CopyFrom(book)
    return req
