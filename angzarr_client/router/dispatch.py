"""Dispatch logic for the unified Router runtime.

In R6 this module provides only the command-handler dispatch path with a
fresh state per call. State rebuild (R7), multi-handler merge (R8), seq
incrementing (R9), rejection (R10), saga/PM/projector (R11-R13) land in
subsequent rounds.
"""

from __future__ import annotations

import inspect
from typing import Any, Callable

import grpc
from google.protobuf.any_pb2 import Any as ProtoAny

from angzarr_client.destinations import Destinations
from angzarr_client.helpers import TYPE_URL_PREFIX

from .responses import ProcessManagerResponse
from angzarr_client.proto.angzarr import (
    BusinessResponse,
    ContextualCommand,
    EventBook,
    EventPage,
    PageHeader,
    ProcessManagerHandleResponse,
    Projection,
    SagaHandleRequest,
    SagaResponse,
    UpcastRequest,
    UpcastResponse,
)
from angzarr_client.proto.angzarr import types_pb2 as _types

_NOTIFICATION_TYPE_URL = TYPE_URL_PREFIX + "angzarr.Notification"


# --------------------------------------------------------------------------
# Error type raised by dispatch for caller-facing problems
# --------------------------------------------------------------------------


class DispatchError(grpc.RpcError):
    """gRPC-compatible dispatch error carrying a StatusCode.

    Raised when a command cannot be routed (unknown type, unknown domain),
    when a request is malformed (missing command/page/cover), or for any
    other caller-facing violation. The gRPC servicer can read ``.code()``
    and forward the status to the client.
    """

    def __init__(self, code: grpc.StatusCode, details: str) -> None:
        super().__init__(details)
        self._code = code
        self._details = details

    def code(self) -> grpc.StatusCode:
        return self._code

    def details(self) -> str:
        return self._details


# --------------------------------------------------------------------------
# Per-class dispatch-table construction
# --------------------------------------------------------------------------


def _collect_methods(instance: Any, sentinel: str) -> list[tuple[Any, Callable]]:
    """Return (marker_value, bound_method) pairs for each method on ``instance``
    that carries ``sentinel`` as an attribute.
    """
    out: list[tuple[Any, Callable]] = []
    cls = type(instance)
    for name in dir(cls):
        # Look up on the class to get the underlying function, then bind.
        attr = cls.__dict__.get(name)
        if attr is None:
            # Walk the MRO to support inherited methods.
            for base in cls.__mro__[1:]:
                attr = base.__dict__.get(name)
                if attr is not None:
                    break
        if attr is None:
            continue
        marker = getattr(attr, sentinel, None)
        if marker is None:
            continue
        bound = getattr(instance, name)
        out.append((marker, bound))
    return out


def _command_handlers_for(instance: Any) -> list[tuple[type, Callable]]:
    """List of ``(command_type, bound_method)`` registered on ``instance``."""
    return _collect_methods(instance, "__angzarr_handles__")


def _rejection_handlers_for(instance: Any) -> list[tuple[tuple[str, str], Callable]]:
    """List of ``((source_domain, command), bound_method)`` registered on ``instance``."""
    return _collect_methods(instance, "__angzarr_rejected__")


def _event_handlers_for(instance: Any) -> list[tuple[type, Callable]]:
    """@handles methods on sagas / PMs / projectors (event dispatch)."""
    return _collect_methods(instance, "__angzarr_handles__")


def _upcasters_for(
    instance: Any,
) -> list[tuple[tuple[type, type], Callable]]:
    """@upcasts methods on an upcaster: ``((from_type, to_type), method)`` pairs."""
    return _collect_methods(instance, "__angzarr_upcasts__")


def _accepts_source_cover(method: Callable) -> bool:
    """Return True if ``method`` declares a ``source_cover`` parameter.

    Saga and process-manager handlers may opt into receiving the source
    event book's ``Cover`` (domain + root + correlation_id) by adding a
    ``source_cover`` keyword parameter. Handlers without it retain the
    original arity.
    """
    try:
        sig = inspect.signature(method)
    except (TypeError, ValueError):
        return False
    return "source_cover" in sig.parameters


def _appliers_for(instance: Any) -> dict[str, Callable]:
    """Map proto full-name suffix → bound applier method for ``instance``."""
    out: dict[str, Callable] = {}
    for event_type, method in _collect_methods(instance, "__angzarr_applies__"):
        out[event_type.DESCRIPTOR.full_name] = method
    return out


def _rebuild_state(instance: Any, prior_events) -> Any:
    """Replay prior events through the instance's @applies methods.

    Returns the mutated state instance. Events with no matching applier are
    silently skipped — appliers are additive, not total.
    """
    state = _build_fresh_state(instance)
    if prior_events is None:
        return state
    appliers = _appliers_for(instance)
    # prior_events is an EventBook; pages carry event Any payloads.
    for page in prior_events.pages:
        if not page.HasField("event"):
            continue
        event_any: ProtoAny = page.event
        # Type URL is "type.googleapis.com/foo.bar.Baz"; strip prefix.
        if not event_any.type_url.startswith(TYPE_URL_PREFIX):
            continue
        suffix = event_any.type_url[len(TYPE_URL_PREFIX) :]
        applier = appliers.get(suffix)
        if applier is None:
            continue
        # Rebuild the event message so the applier receives a typed instance.
        for event_type, _ in _collect_methods(instance, "__angzarr_applies__"):
            if event_type.DESCRIPTOR.full_name == suffix:
                evt = event_type()
                evt.ParseFromString(event_any.value)
                applier(state, evt)
                break
    return state


# --------------------------------------------------------------------------
# Dispatch: command-handler path
# --------------------------------------------------------------------------


def dispatch_command(
    handlers: list[Any], request: ContextualCommand
) -> BusinessResponse:
    """Dispatch a ContextualCommand to the matching @handles or @rejected method.

    Command path (R6-R9): routes by (domain, type_url).
    Notification path (R10): routes rejection compensation to @rejected handlers.
    Multi-handler merge applies to both paths.
    """
    cmd_book = request.command
    if not cmd_book.pages:
        raise DispatchError(
            grpc.StatusCode.INVALID_ARGUMENT,
            "CommandBook has no pages",
        )

    domain = cmd_book.cover.domain
    if not domain:
        raise DispatchError(
            grpc.StatusCode.INVALID_ARGUMENT,
            "CommandBook cover has no domain",
        )

    cmd_page = cmd_book.pages[0]
    command_any: ProtoAny = cmd_page.command
    type_url = command_any.type_url
    if not type_url:
        raise DispatchError(
            grpc.StatusCode.INVALID_ARGUMENT,
            "CommandPage has no command Any",
        )

    # Notification branch: compensation for a rejected upstream command.
    if type_url == _NOTIFICATION_TYPE_URL:
        return _dispatch_notification(handlers, command_any, request)

    # Find all handlers registered for (domain, type_url-suffix). R6 uses the
    # first match only; R8 iterates all of them.
    matched: list[tuple[Any, type, Callable]] = []
    for inst in handlers:
        if type(inst).__angzarr_meta__.get("domain") != domain:
            continue
        for cmd_type, method in _command_handlers_for(inst):
            if type_url == TYPE_URL_PREFIX + cmd_type.DESCRIPTOR.full_name:
                matched.append((inst, cmd_type, method))

    if not matched:
        raise DispatchError(
            grpc.StatusCode.INVALID_ARGUMENT,
            f"no handler registered for domain={domain!r} type_url={type_url!r}",
        )

    # Rebuild state per instance (isolated). Invoke all matched handlers in
    # registration order, advancing the sequence counter across the merged
    # output so handler N sees seq = base_seq + events_emitted_by_earlier_handlers.
    prior = request.events if request.HasField("events") else None
    base_seq = int(request.events.next_sequence) if request.HasField("events") else 0
    current_seq = base_seq
    merged = EventBook()

    for inst, cmd_type, method in matched:
        # Decode the command per-instance so each handler receives its own
        # independent parsed message (no shared mutation risk if anyone modifies it).
        cmd = cmd_type()
        cmd.ParseFromString(command_any.value)

        state = _rebuild_state(inst, prior)
        emitted = method(cmd, state, current_seq)

        pages = _pack_events(emitted, base_seq=current_seq).pages
        merged.pages.extend(pages)
        current_seq += len(pages)

    response = BusinessResponse()
    response.events.CopyFrom(merged)
    return response


def _dispatch_notification(
    handlers: list[Any], command_any: ProtoAny, request: ContextualCommand
) -> BusinessResponse:
    """Route a Notification to all matching @rejected handlers; merge compensation."""
    notification = _types.Notification()
    notification.ParseFromString(command_any.value)

    # Unpack RejectionNotification from the notification's payload Any.
    rejection = _types.RejectionNotification()
    if notification.HasField("payload"):
        notification.payload.Unpack(rejection)

    # Extract (domain, command-suffix) from the rejected command book.
    source_domain = ""
    cmd_suffix = ""
    if rejection.HasField("rejected_command") and rejection.rejected_command.pages:
        rb = rejection.rejected_command
        if rb.HasField("cover"):
            source_domain = rb.cover.domain
        first = rb.pages[0]
        if first.HasField("command"):
            rc_type_url = first.command.type_url
            # "type.googleapis.com/foo.bar.Baz" → "Baz"
            if rc_type_url:
                full_name = rc_type_url.rsplit("/", 1)[-1]
                cmd_suffix = full_name.rsplit(".", 1)[-1]

    prior = request.events if request.HasField("events") else None
    merged = EventBook()
    current_seq = int(request.events.next_sequence) if request.HasField("events") else 0

    for inst in handlers:
        for (d, c), method in _rejection_handlers_for(inst):
            if d == source_domain and c == cmd_suffix:
                state = _rebuild_state(inst, prior)
                emitted = method(notification, state)
                pages = _pack_events(emitted, base_seq=current_seq).pages
                merged.pages.extend(pages)
                current_seq += len(pages)

    response = BusinessResponse()
    response.events.CopyFrom(merged)
    return response


def _build_fresh_state(instance: Any) -> Any:
    """Construct a fresh state object for ``instance``.

    R6: honor ``@state_factory`` method if present, else call state type's
    default constructor. R7 wires this into full event-replay rebuild.
    """
    cls = type(instance)
    for name in dir(cls):
        attr = cls.__dict__.get(name)
        if attr is None:
            continue
        if getattr(attr, "__angzarr_state_factory__", None):
            return attr(instance)
    state_type = cls.__angzarr_meta__["state"]
    return state_type()


def dispatch_saga(handlers: list[Any], request: SagaHandleRequest) -> SagaResponse:
    """Dispatch a source event through registered saga handlers.

    The trigger is the last event in ``request.source``; sagas whose
    ``@saga(source=...)`` matches the source book's domain AND whose
    ``@handles(Evt)`` matches the trigger's proto type are all invoked
    in registration order. Emitted commands + fact books are merged.
    """
    source = request.source
    source_cover = source.cover if source.HasField("cover") else None
    source_domain = source_cover.domain if source_cover is not None else ""
    if not source.pages:
        return SagaResponse()

    trigger = source.pages[-1]
    if not trigger.HasField("event"):
        return SagaResponse()

    type_url = trigger.event.type_url
    if not type_url or not type_url.startswith(TYPE_URL_PREFIX):
        return SagaResponse()
    suffix = type_url[len(TYPE_URL_PREFIX) :]

    destinations = Destinations.from_proto(request.destination_sequences)

    response = SagaResponse()
    for inst in handlers:
        if type(inst).__angzarr_meta__.get("source") != source_domain:
            continue
        for evt_type, method in _event_handlers_for(inst):
            if evt_type.DESCRIPTOR.full_name != suffix:
                continue
            # Decode the trigger event.
            evt = evt_type()
            evt.ParseFromString(trigger.event.value)
            if _accepts_source_cover(method):
                emitted = method(evt, destinations, source_cover=source_cover)
            else:
                emitted = method(evt, destinations)
            _merge_saga_output(emitted, response)
    return response


def dispatch_process_manager(
    handlers: list[Any], request: Any
) -> ProcessManagerHandleResponse:
    """Dispatch a trigger event through registered process-manager handlers.

    Trigger is the last event in ``request.trigger``; PMs whose ``sources``
    include the trigger's domain AND whose ``@handles(Evt)`` matches the
    trigger type are invoked in registration order. Each PM rebuilds its
    own state from ``request.process_state`` via its ``@applies`` methods.
    ``ProcessManagerResponse`` returns merged into a single
    ``ProcessManagerHandleResponse``.
    """
    trigger = request.trigger
    trigger_cover = trigger.cover if trigger.HasField("cover") else None
    trigger_domain = trigger_cover.domain if trigger_cover is not None else ""
    if not trigger.pages:
        return ProcessManagerHandleResponse()

    last = trigger.pages[-1]
    if not last.HasField("event"):
        return ProcessManagerHandleResponse()
    type_url = last.event.type_url
    if not type_url or not type_url.startswith(TYPE_URL_PREFIX):
        return ProcessManagerHandleResponse()
    suffix = type_url[len(TYPE_URL_PREFIX) :]

    destinations = Destinations.from_proto(request.destination_sequences)
    process_state = request.process_state if request.HasField("process_state") else None

    response = ProcessManagerHandleResponse()
    merged_process_events = EventBook()
    has_pe = False
    for inst in handlers:
        meta = type(inst).__angzarr_meta__
        sources = meta.get("sources", [])
        if trigger_domain not in sources:
            continue
        # Match the handler by event type.
        for evt_type, method in _event_handlers_for(inst):
            if evt_type.DESCRIPTOR.full_name != suffix:
                continue
            state = _rebuild_state(inst, process_state)
            evt = evt_type()
            evt.ParseFromString(last.event.value)
            if _accepts_source_cover(method):
                result = method(evt, state, destinations, source_cover=trigger_cover)
            else:
                result = method(evt, state, destinations)
            if result is None:
                continue
            if not isinstance(result, ProcessManagerResponse):
                raise DispatchError(
                    grpc.StatusCode.INTERNAL,
                    f"PM handler must return ProcessManagerResponse, got {type(result).__name__}",
                )
            for cmd in result.commands or []:
                response.commands.append(cmd)
            for fact in result.facts or []:
                response.facts.append(fact)
            if result.process_events is not None:
                merged_process_events.pages.extend(result.process_events.pages)
                if not has_pe:
                    merged_process_events.cover.CopyFrom(result.process_events.cover)
                    has_pe = True
    if has_pe:
        response.process_events.CopyFrom(merged_process_events)
    return response


def dispatch_projector(handlers: list[Any], events: EventBook) -> Projection:
    """Fan out each event in ``events`` to matching @handles methods on all
    registered projectors that declare the source domain.

    Projector handlers are side-effect only — their return value is ignored.
    Returns a ``Projection`` with the source cover copied through for
    framework bookkeeping.
    """
    domain = events.cover.domain if events.HasField("cover") else ""

    for page in events.pages:
        if not page.HasField("event"):
            continue
        type_url = page.event.type_url
        if not type_url or not type_url.startswith(TYPE_URL_PREFIX):
            continue
        suffix = type_url[len(TYPE_URL_PREFIX) :]

        for inst in handlers:
            meta = type(inst).__angzarr_meta__
            if domain not in meta.get("domains", []):
                continue
            for evt_type, method in _event_handlers_for(inst):
                if evt_type.DESCRIPTOR.full_name != suffix:
                    continue
                evt = evt_type()
                evt.ParseFromString(page.event.value)
                method(evt)

    result = Projection()
    if events.HasField("cover"):
        result.cover.CopyFrom(events.cover)
    return result


def _merge_saga_output(emitted: Any, response: SagaResponse) -> None:
    """Merge a saga handler's return value into a SagaResponse.

    Accepts ``None``, a single ``CommandBook`` / ``EventBook``, or a
    tuple/list thereof. CommandBooks → response.commands; EventBooks
    (facts) → response.events.
    """
    from angzarr_client.proto.angzarr import CommandBook as _CB

    if emitted is None:
        return
    items = list(emitted) if isinstance(emitted, (list, tuple)) else [emitted]
    for item in items:
        if isinstance(item, _CB):
            response.commands.append(item)
        elif isinstance(item, EventBook):
            response.events.append(item)
        else:
            raise DispatchError(
                grpc.StatusCode.INTERNAL,
                f"saga handler returned unsupported type: {type(item).__name__}",
            )


def _pack_events(emitted: Any, base_seq: int) -> EventBook:
    """Pack handler return value(s) into an EventBook.

    Accepts:
      - a single proto message → one-page EventBook
      - a tuple/list of proto messages → multi-page EventBook
    """
    if emitted is None:
        return EventBook()
    if isinstance(emitted, (list, tuple)):
        messages = list(emitted)
    else:
        messages = [emitted]

    book = EventBook()
    for offset, msg in enumerate(messages):
        page = EventPage()
        page.header.CopyFrom(PageHeader(sequence=base_seq + offset))
        any_msg = ProtoAny()
        any_msg.type_url = TYPE_URL_PREFIX + msg.DESCRIPTOR.full_name
        any_msg.value = msg.SerializeToString()
        page.event.CopyFrom(any_msg)
        book.pages.append(page)
    return book


def dispatch_upcaster(
    handlers: list[Any], request: UpcastRequest
) -> UpcastResponse:
    """Transform events via registered @upcaster handlers.

    Only handlers whose ``@upcaster(domain=...)`` matches ``request.domain``
    are consulted. For each event page, the first matching ``@upcasts(From, To)``
    method (exact proto type-URL match on ``From``) is invoked; its return value
    is packed into a new ``Any`` preserving the original ``PageHeader`` and
    ``created_at`` timestamp. Events with no matching transform pass through
    unchanged. Events already at a target version pass through.
    """
    out_events: list[EventPage] = []
    for page in request.events:
        if not page.HasField("event"):
            out_events.append(page)
            continue
        event_any: ProtoAny = page.event
        transformed = event_any
        for inst in handlers:
            meta = type(inst).__angzarr_meta__
            if meta.get("domain") != request.domain:
                continue
            matched = False
            for (from_type, _to_type), method in _upcasters_for(inst):
                expected = TYPE_URL_PREFIX + from_type.DESCRIPTOR.full_name
                if event_any.type_url != expected:
                    continue
                old_evt = from_type()
                event_any.Unpack(old_evt)
                new_evt = method(old_evt)
                new_any = ProtoAny()
                new_any.type_url = (
                    TYPE_URL_PREFIX + new_evt.DESCRIPTOR.full_name
                )
                new_any.value = new_evt.SerializeToString()
                transformed = new_any
                matched = True
                break
            if matched:
                break
        new_page = EventPage()
        new_page.CopyFrom(page)
        new_page.event.CopyFrom(transformed)
        out_events.append(new_page)
    return UpcastResponse(events=out_events)
