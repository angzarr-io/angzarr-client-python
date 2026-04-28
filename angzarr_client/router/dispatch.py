"""Dispatch logic for the unified Router runtime.

In R6 this module provides only the command-handler dispatch path with a
fresh state per call. State rebuild (R7), multi-handler merge (R8), seq
incrementing (R9), rejection (R10), saga/PM/projector (R11-R13) land in
subsequent rounds.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any, Callable

import grpc
from google.protobuf.any_pb2 import Any as ProtoAny

from ..error_codes import codes, keys, messages

_LOG = logging.getLogger(__name__)

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

_NOTIFICATION_TYPE_URL = TYPE_URL_PREFIX + "angzarr_client.proto.angzarr.Notification"

# (handler_class, zero-arg factory returning a fresh/pooled handler instance).
Factory = tuple[type, Callable[[], Any]]


# --------------------------------------------------------------------------
# Error type raised by dispatch for caller-facing problems
# --------------------------------------------------------------------------


class DispatchError(grpc.RpcError):
    """gRPC-compatible dispatch error carrying a StatusCode.

    Raised when a command cannot be routed (unknown type, unknown domain),
    when a request is malformed (missing command/page/cover), or for any
    other caller-facing violation. The gRPC servicer can read ``.code()``
    and forward the status to the client.

    Audit finding #59: ``message`` is a static string (no interpolation);
    runtime context like type URLs, domains, etc. ride in ``extras``.
    The SCREAMING_SNAKE ``error_code`` is a stable cross-language
    identifier suitable for cucumber assertions.
    """

    def __init__(
        self,
        code: grpc.StatusCode,
        details: str,
        *,
        error_code: str = "",
        extras: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(details)
        self._code = code
        self._details = details
        self.error_code = error_code
        self.extras: dict[str, str] = {
            k: str(v) for k, v in (extras or {}).items()
        }

    def code(self) -> grpc.StatusCode:
        return self._code

    def details(self) -> str:
        return self._details


# --------------------------------------------------------------------------
# Per-class dispatch-table construction
# --------------------------------------------------------------------------


def _collect_method_names(cls: type, sentinel: str) -> list[tuple[Any, str]]:
    """Return ``(marker_value, method_name)`` pairs for each method on ``cls``
    whose underlying function carries ``sentinel`` as an attribute.

    Walks the MRO so inherited decorated methods are picked up. Returning
    names (not bound methods) lets callers defer instance construction until
    after the outer filter has matched on class-level metadata.
    """
    out: list[tuple[Any, str]] = []
    for name in dir(cls):
        attr = cls.__dict__.get(name)
        if attr is None:
            for base in cls.__mro__[1:]:
                attr = base.__dict__.get(name)
                if attr is not None:
                    break
        if attr is None:
            continue
        marker = getattr(attr, sentinel, None)
        if marker is None:
            continue
        out.append((marker, name))
    return out


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


def _accepts_source_seq(method: Callable) -> bool:
    """Return True if ``method`` declares a ``source_seq`` parameter.

    Sagas that opt into ``AngzarrDeferredSequence`` headers need the
    source event's sequence to populate the deferred provenance. The
    framework dedupes saga-produced commands on
    ``(source.root, source_seq, target.root)`` so AMQP redelivery of the
    triggering event becomes a no-op at the aggregate pipeline.
    """
    try:
        sig = inspect.signature(method)
    except (TypeError, ValueError):
        return False
    return "source_seq" in sig.parameters


def _trigger_sequence(trigger_page) -> int:
    """Extract the explicit sequence from a trigger's PageHeader.

    Sagas reacting to a normal aggregate event always see an explicit
    ``sequence`` on the source page; defer/external pages produce events
    too but the aggregate sequence is what matters for downstream dedup.
    Returns 0 if no header is present (defensive — shouldn't happen in
    a well-formed EventBook).
    """
    if not trigger_page.HasField("header"):
        return 0
    header = trigger_page.header
    if header.HasField("sequence"):
        return int(header.sequence)
    if header.HasField("external_deferred"):
        return 0
    if header.HasField("angzarr_deferred"):
        return int(header.angzarr_deferred.source_seq)
    return 0


def _appliers_for(instance: Any) -> dict[str, Callable]:
    """Map proto full-name suffix → bound applier method for ``instance``."""
    out: dict[str, Callable] = {}
    cls = type(instance)
    for event_type, method_name in _collect_method_names(cls, "__angzarr_applies__"):
        out[event_type.DESCRIPTOR.full_name] = getattr(instance, method_name)
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
    cls = type(instance)
    applier_types = _collect_method_names(cls, "__angzarr_applies__")
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
        for event_type, _ in applier_types:
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
    factories: list[Factory], request: ContextualCommand
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
            messages.MISSING_COMMAND_PAGE,
            error_code=codes.MISSING_COMMAND_PAGE,
        )

    domain = cmd_book.cover.domain
    if not domain:
        raise DispatchError(
            grpc.StatusCode.INVALID_ARGUMENT,
            messages.MISSING_COMMAND_BOOK,
            error_code=codes.MISSING_COMMAND_BOOK,
        )

    cmd_page = cmd_book.pages[0]
    command_any: ProtoAny = cmd_page.command
    type_url = command_any.type_url
    if not type_url:
        raise DispatchError(
            grpc.StatusCode.INVALID_ARGUMENT,
            messages.MISSING_COMMAND_PAYLOAD,
            error_code=codes.MISSING_COMMAND_PAYLOAD,
        )

    # Notification branch: compensation for a rejected upstream command.
    if type_url == _NOTIFICATION_TYPE_URL:
        return _dispatch_notification(factories, command_any, request)

    # Find all factories whose class handles (domain, type_url-suffix). Matching
    # is pure class-level reflection; handlers aren't instantiated until we
    # know they'll be invoked.
    matched: list[tuple[Callable[[], Any], type, str]] = []
    for cls, factory in factories:
        if cls.__angzarr_meta__.get("domain") != domain:
            continue
        for cmd_type, method_name in _collect_method_names(cls, "__angzarr_handles__"):
            if type_url == TYPE_URL_PREFIX + cmd_type.DESCRIPTOR.full_name:
                matched.append((factory, cmd_type, method_name))

    if not matched:
        raise DispatchError(
            grpc.StatusCode.INVALID_ARGUMENT,
            messages.NO_HANDLER_REGISTERED,
            error_code=codes.NO_HANDLER_REGISTERED,
            extras={keys.DOMAIN: domain, keys.TYPE_URL: type_url},
        )

    # Rebuild state per instance (isolated). Invoke all matched handlers in
    # registration order, advancing the sequence counter across the merged
    # output so handler N sees seq = base_seq + events_emitted_by_earlier_handlers.
    prior = request.events if request.HasField("events") else None
    base_seq = int(request.events.next_sequence) if request.HasField("events") else 0
    current_seq = base_seq
    merged = EventBook()

    for factory, cmd_type, method_name in matched:
        # Decode the command per-instance so each handler receives its own
        # independent parsed message (no shared mutation risk if anyone modifies it).
        cmd = cmd_type()
        cmd.ParseFromString(command_any.value)

        inst = factory()
        state = _rebuild_state(inst, prior)
        emitted = getattr(inst, method_name)(cmd, state, current_seq)

        pages = _pack_events(emitted, base_seq=current_seq).pages
        merged.pages.extend(pages)
        current_seq += len(pages)

    response = BusinessResponse()
    response.events.CopyFrom(merged)
    return response


def _dispatch_notification(
    factories: list[Factory], command_any: ProtoAny, request: ContextualCommand
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

    for cls, factory in factories:
        for (d, c), method_name in _collect_method_names(cls, "__angzarr_rejected__"):
            if d == source_domain and c == cmd_suffix:
                inst = factory()
                state = _rebuild_state(inst, prior)
                emitted = getattr(inst, method_name)(notification, state)
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


def dispatch_saga(factories: list[Factory], request: SagaHandleRequest) -> SagaResponse:
    """Dispatch a source event through registered saga handlers.

    The trigger is the last event in ``request.source``; sagas whose
    ``@saga(source=...)`` matches the source book's domain AND whose
    ``@handles(Evt)`` matches the trigger's proto type are all invoked
    in registration order. Emitted commands + fact books are merged.
    """
    # Raise on malformed input to match Rust's
    # `extract_saga_event_type_url` (`runtime.rs:301-317`). Silent return
    # would let the framework move on, but the audit (P2.5 / finding
    # #12) flagged the divergence: malformed requests indicate either a
    # framework bug or a wire-protocol violation, neither of which
    # should be silently absorbed by the dispatcher. Per audit decision
    # 2026-04-25 (explicit failure over tolerance).
    if not request.HasField("source"):
        raise DispatchError(
            grpc.StatusCode.INVALID_ARGUMENT,
            messages.MISSING_SAGA_SOURCE,
            error_code=codes.MISSING_SAGA_SOURCE,
        )
    source = request.source
    source_cover = source.cover if source.HasField("cover") else None
    source_domain = source_cover.domain if source_cover is not None else ""
    if not source.pages:
        raise DispatchError(
            grpc.StatusCode.INVALID_ARGUMENT,
            messages.EMPTY_SAGA_SOURCE,
            error_code=codes.EMPTY_SAGA_SOURCE,
        )

    trigger = source.pages[-1]
    if not trigger.HasField("event"):
        raise DispatchError(
            grpc.StatusCode.INVALID_ARGUMENT,
            messages.MISSING_SAGA_EVENT_PAYLOAD,
            error_code=codes.MISSING_SAGA_EVENT_PAYLOAD,
        )

    type_url = trigger.event.type_url
    if not type_url or not type_url.startswith(TYPE_URL_PREFIX):
        raise DispatchError(
            grpc.StatusCode.INVALID_ARGUMENT,
            messages.SAGA_INVALID_TYPE_URL,
            error_code=codes.SAGA_INVALID_TYPE_URL,
            extras={keys.TYPE_URL: type_url},
        )
    suffix = type_url[len(TYPE_URL_PREFIX) :]

    destinations = Destinations.from_proto(request.destination_sequences)

    source_seq = _trigger_sequence(trigger)

    response = SagaResponse()
    matched = 0
    for cls, factory in factories:
        if cls.__angzarr_meta__.get("source") != source_domain:
            continue
        for evt_type, method_name in _collect_method_names(cls, "__angzarr_handles__"):
            if evt_type.DESCRIPTOR.full_name != suffix:
                continue
            inst = factory()
            # Decode the trigger event.
            evt = evt_type()
            evt.ParseFromString(trigger.event.value)
            method = getattr(inst, method_name)
            kwargs = {}
            if _accepts_source_cover(method):
                kwargs["source_cover"] = source_cover
            if _accepts_source_seq(method):
                kwargs["source_seq"] = source_seq
            emitted = method(evt, destinations, **kwargs)
            _merge_saga_output(emitted, response)
            matched += 1

    # P2.5 / audit finding #36: "no handler matched" is a normal runtime
    # condition (no saga subscribed to this event type), not a failure.
    # Log at info-level for observability and return the empty
    # SagaResponse — Rust matches via the same convention.
    if matched == 0:
        _LOG.info(
            "no saga handler registered for event type %r — returning empty SagaResponse",
            type_url,
        )

    return response


def dispatch_process_manager(
    factories: list[Factory], request: Any
) -> ProcessManagerHandleResponse:
    """Dispatch a trigger event through registered process-manager handlers.

    Trigger is the last event in ``request.trigger``; PMs whose ``sources``
    include the trigger's domain AND whose ``@handles(Evt)`` matches the
    trigger type are invoked in registration order. Each PM rebuilds its
    own state from ``request.process_state`` via its ``@applies`` methods.
    ``ProcessManagerResponse`` returns merged into a single
    ``ProcessManagerHandleResponse``.
    """
    # Audit finding #37 (Option A — explicit-over-tolerant, per P2.5):
    # malformed PM triggers raise DispatchError(INVALID_ARGUMENT) instead
    # of returning silently. Mirrors saga dispatch's four-case validation
    # (`extract_saga_event_type_url` / `dispatch_saga`). A framework bug
    # or wire-protocol violation surfaces loudly rather than disappearing
    # into an empty response.
    if not request.HasField("trigger"):
        raise DispatchError(
            grpc.StatusCode.INVALID_ARGUMENT,
            messages.MISSING_PM_TRIGGER,
            error_code=codes.MISSING_PM_TRIGGER,
        )
    trigger = request.trigger
    trigger_cover = trigger.cover if trigger.HasField("cover") else None
    trigger_domain = trigger_cover.domain if trigger_cover is not None else ""
    if not trigger.pages:
        raise DispatchError(
            grpc.StatusCode.INVALID_ARGUMENT,
            messages.EMPTY_PM_TRIGGER,
            error_code=codes.EMPTY_PM_TRIGGER,
        )

    last = trigger.pages[-1]
    if not last.HasField("event"):
        raise DispatchError(
            grpc.StatusCode.INVALID_ARGUMENT,
            messages.MISSING_PM_EVENT_PAYLOAD,
            error_code=codes.MISSING_PM_EVENT_PAYLOAD,
        )
    type_url = last.event.type_url
    if not type_url or not type_url.startswith(TYPE_URL_PREFIX):
        raise DispatchError(
            grpc.StatusCode.INVALID_ARGUMENT,
            messages.PM_INVALID_TYPE_URL,
            error_code=codes.PM_INVALID_TYPE_URL,
            extras={keys.TYPE_URL: type_url},
        )
    suffix = type_url[len(TYPE_URL_PREFIX) :]

    destinations = Destinations.from_proto(request.destination_sequences)
    process_state = request.process_state if request.HasField("process_state") else None

    response = ProcessManagerHandleResponse()
    merged_process_events = EventBook()
    has_pe = False
    matched = 0
    for cls, factory in factories:
        meta = cls.__angzarr_meta__
        sources = meta.get("sources", [])
        if trigger_domain not in sources:
            continue
        # Match the handler by event type.
        for evt_type, method_name in _collect_method_names(cls, "__angzarr_handles__"):
            if evt_type.DESCRIPTOR.full_name != suffix:
                continue
            inst = factory()
            state = _rebuild_state(inst, process_state)
            evt = evt_type()
            evt.ParseFromString(last.event.value)
            method = getattr(inst, method_name)
            if _accepts_source_cover(method):
                result = method(evt, state, destinations, source_cover=trigger_cover)
            else:
                result = method(evt, state, destinations)
            matched += 1
            if result is None:
                continue
            if not isinstance(result, ProcessManagerResponse):
                raise DispatchError(
                    grpc.StatusCode.INTERNAL,
                    messages.PM_HANDLER_WRONG_RETURN_TYPE,
                    error_code=codes.PM_HANDLER_WRONG_RETURN_TYPE,
                    extras={keys.ACTUAL_RETURN_TYPE: type(result).__name__},
                )
            for cmd in result.commands or []:
                response.commands.append(cmd)
            for fact in result.facts or []:
                response.facts.append(fact)
            # Audit finding #56 (Option B): `process_events` is now a
            # list[EventBook]. Iterate; concatenate every book's pages
            # into the wire's single `process_events`. First non-empty
            # book's cover wins, matching the existing first-cover
            # convention.
            for pe_book in result.process_events or []:
                merged_process_events.pages.extend(pe_book.pages)
                if not has_pe:
                    merged_process_events.cover.CopyFrom(pe_book.cover)
                    has_pe = True
    if has_pe:
        response.process_events.CopyFrom(merged_process_events)

    # Audit findings #36/#37: "no handler matched" is a normal runtime
    # condition (no PM subscribed to this trigger). Log at info-level for
    # observability and return the empty response — Rust matches via the
    # same convention.
    if matched == 0:
        _LOG.info(
            "no process-manager handler registered for event type %r — returning empty response",
            type_url,
        )

    return response


def dispatch_projector(factories: list[Factory], events: EventBook) -> Projection:
    """Fan out each event in ``events`` to matching @handles methods on all
    registered projectors that declare the source domain.

    Projector handlers are side-effect only — their return value is ignored.
    Returns a ``Projection`` with the source cover copied through for
    framework bookkeeping.

    One handler instance is constructed per matching projector per dispatch
    call (via its factory) and reused across every event in the book, so a
    projector can accumulate state within a single projection run.
    """
    domain = events.cover.domain if events.HasField("cover") else ""

    # Instantiate each matching projector once per dispatch. Non-matching
    # projectors never have their factory invoked. A declared "*" domain
    # is the catch-all wildcard — matches any incoming book domain.
    # Mirrors Rust's `runtime.rs:428` (`x == d || x == "*"`). Audit
    # finding #53.
    live: list[tuple[Any, list[tuple[type, str]]]] = []
    for cls, factory in factories:
        meta = cls.__angzarr_meta__
        declared = meta.get("domains", [])
        if "*" not in declared and domain not in declared:
            continue
        method_table = _collect_method_names(cls, "__angzarr_handles__")
        if not method_table:
            continue
        live.append((factory(), method_table))

    for page in events.pages:
        if not page.HasField("event"):
            continue
        type_url = page.event.type_url
        if not type_url or not type_url.startswith(TYPE_URL_PREFIX):
            continue
        suffix = type_url[len(TYPE_URL_PREFIX) :]

        for inst, method_table in live:
            for evt_type, method_name in method_table:
                if evt_type.DESCRIPTOR.full_name != suffix:
                    continue
                evt = evt_type()
                evt.ParseFromString(page.event.value)
                getattr(inst, method_name)(evt)

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
                messages.SAGA_HANDLER_UNSUPPORTED_RETURN_TYPE,
                error_code=codes.SAGA_HANDLER_UNSUPPORTED_RETURN_TYPE,
                extras={keys.ACTUAL_RETURN_TYPE: type(item).__name__},
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
    factories: list[Factory], request: UpcastRequest
) -> UpcastResponse:
    """Transform events via registered @upcaster handlers.

    Only handlers whose ``@upcaster(domain=...)`` matches ``request.domain``
    are consulted. **Every matching upcaster runs in registration order; the
    output of one is the input of the next** — chained transforms let
    schema evolution compose across versions (V1 → V2 → V3) without
    forcing each upcaster to know about every newer version. The
    ``@upcasts(From, To)`` match is exact on the *current* event's
    type_url, so an upcaster only fires when its `From` matches the
    incoming/transformed value. Events with no matching transform pass
    through unchanged.

    Audit finding #43 (cucumber C-0136 / C-0137): mirrors Rust's
    ``router/upcaster.rs::dispatch`` semantics. Was previously
    first-match-wins (broke after first transform).
    """
    out_events: list[EventPage] = []
    for page in request.events:
        if not page.HasField("event"):
            out_events.append(page)
            continue
        # `current` is the running event Any — each matching upcaster
        # transforms it and the next factory sees the new value.
        current = ProtoAny()
        current.CopyFrom(page.event)
        for cls, factory in factories:
            meta = cls.__angzarr_meta__
            if meta.get("domain") != request.domain:
                continue
            for (from_type, _to_type), method_name in _collect_method_names(
                cls, "__angzarr_upcasts__"
            ):
                expected = TYPE_URL_PREFIX + from_type.DESCRIPTOR.full_name
                if current.type_url != expected:
                    continue
                inst = factory()
                old_evt = from_type()
                current.Unpack(old_evt)
                new_evt = getattr(inst, method_name)(old_evt)
                new_any = ProtoAny()
                new_any.type_url = TYPE_URL_PREFIX + new_evt.DESCRIPTOR.full_name
                new_any.value = new_evt.SerializeToString()
                current = new_any
                # One method per upcaster fires per page; move to the
                # next factory with the (possibly-updated) `current`.
                break
        new_page = EventPage()
        new_page.CopyFrom(page)
        new_page.event.CopyFrom(current)
        out_events.append(new_page)
    return UpcastResponse(events=out_events)
