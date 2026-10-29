"""The Python binding core: registration API, dispatch marshaling, and the
single cffi callback trampoline over the router-ffi C ABI (ABI 4).

The router owns every component's state: an aggregate's or process
manager's state and a projector's projection cross the boundary as a
serialized ``google.protobuf.Any`` of the registered message type, and every
host callback is stateless. The binding decodes the state a callback is
handed into a fresh instance of the component's message, runs the typed
thunks over it, and re-encodes it (a fold) or reads it (decide, fact and
process-manager handlers). Nothing is kept between or within dispatches but
the callback table, keyed by the callback id each registration assigns.
"""

from __future__ import annotations

import enum
import itertools
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from google.protobuf import any_pb2
from google.protobuf.message import DecodeError, Message
from google.rpc import error_details_pb2, status_pb2

from ..proto.io.angzarr.router.ffi.v1 import abi_pb2
from ..proto.io.angzarr.v1 import (
    command_handler_pb2,
    process_manager_pb2,
    saga_pb2,
    types_pb2,
)
from ._abi import ffi, lib

if TYPE_CHECKING:
    from typing_extensions import Self

# --- ABI status codes (mirror crates/router-ffi/src/abi.rs) ---
_STATUS_OK = 0  # success with a payload in `out`
_STATUS_OK_EMPTY = 1  # success, handler emitted nothing

# The code an unclassified handler failure surfaces as — the binding's job
# to classify, mirroring the Go binding and client-* engines.
CODE_UNHANDLED_HANDLER_ERROR = "UNHANDLED_HANDLER_ERROR"

# The reverse-DNS error domain on every ErrorInfo the boundary emits
# (distinct from the io.angzarr proto package — see plan §1).
ERROR_INFO_DOMAIN = "angzarr.io"

# The code a second component's registration is refused with (one router
# hosts one component).
CODE_DUPLICATE_REGISTRATION = "DUPLICATE_REGISTRATION"

# The code AnyDecodeError carries.
CODE_ANY_DECODE_FAILED = "ANY_DECODE_FAILED"

# The framework's Any type-URL convention: a bare "/" followed by the
# fully-qualified message name (NOT the type.googleapis.com prefix Any.Pack
# defaults to). The core keys event/command dispatch on it.
_FRAMEWORK_ANY_PREFIX = "/"


class GrpcCode(enum.IntEnum):
    """The numeric gRPC status codes carried with a coded error. Plain ints
    so the binding depends only on the protobuf runtime, not grpcio."""

    INVALID_ARGUMENT = 3
    NOT_FOUND = 5
    FAILED_PRECONDITION = 9
    ABORTED = 10
    UNIMPLEMENTED = 12
    INTERNAL = 13
    DATA_LOSS = 15


class CodedError(Exception):
    """A stable cross-language coded failure. A handler raises one of its two
    classes, :class:`Rejection` or :class:`Bug`, to fail a command with a
    code like ``VALUE_NOT_POSITIVE``;
    the binding also raises one when decoding a coded failure the core
    returned (``NO_HANDLER_REGISTERED``, ``CONFLICT_UNRESOLVED``, …). It
    crosses the FFI as ``google.rpc.Status`` carrying a
    ``google.rpc.ErrorInfo``."""

    def __init__(
        self,
        code: str = "",
        message: str = "",
        grpc: int = GrpcCode.INTERNAL,
        extras: dict[str, str] | None = None,
    ):
        self.code = code
        self.message = message
        self.grpc = int(grpc)
        self.extras = dict(extras) if extras else {}
        super().__init__(f"{code}: {message}" if code else message)


class Rejection(CodedError):
    """A business rejection: the command is refused, a business "no", never
    a fault. One of the two classes a handler fails with (the other is
    :class:`Bug`). A diagnostic key and its args, never text (decision
    14.37), crossing as FAILED_PRECONDITION under the key; it reaches the
    issuing policy's OnRefused."""

    def __init__(self, key: str, **args: object) -> None:
        super().__init__(
            code=key,
            message=key,
            grpc=GrpcCode.FAILED_PRECONDITION,
            extras={name: str(value) for name, value in args.items()},
        )


class Bug(CodedError):
    """A bug: a defect, never a business answer. The other of the two
    classes a handler fails with (the first is :class:`Rejection`). A
    diagnostic key and its args, crossing as INTERNAL
    ``UNHANDLED_HANDLER_ERROR`` with the key as ``cause`` and each arg as
    ``cause.<arg>`` (decision 14.37); the runtime alerts it and
    dead-letters the input."""

    def __init__(self, key: str, **args: object) -> None:
        cause = {"cause": key}
        cause.update({f"cause.{name}": str(value) for name, value in args.items()})
        super().__init__(
            code=CODE_UNHANDLED_HANDLER_ERROR,
            message=key,
            grpc=GrpcCode.INTERNAL,
            extras=cause,
        )


def any_decode_error(type_url: str, cause: BaseException) -> CodedError:
    """Report that a google.protobuf.Any payload failed to parse to its
    expected type. Generated dispatch thunks raise it when a command or event
    Any cannot be decoded — a malformed payload is an invalid argument, not a
    handler bug."""
    return CodedError(
        code=CODE_ANY_DECODE_FAILED,
        message=f"decode Any {type_url!r}: {cause}",
        grpc=GrpcCode.INVALID_ARGUMENT,
        extras={"type_url": type_url},
    )


def pack(msg) -> any_pb2.Any:
    """Wrap a message in a google.protobuf.Any using the framework's bare-"/"
    type-URL convention. Generated typed-emit wiring uses it to build an
    EventBook from the typed events a command handler returns."""
    return any_pb2.Any(
        type_url=_FRAMEWORK_ANY_PREFIX + msg.DESCRIPTOR.full_name,
        value=msg.SerializeToString(),
    )


def _as_any(msg) -> any_pb2.Any:
    """``msg`` itself when it is an Any, otherwise ``msg`` packed."""
    return msg if isinstance(msg, any_pb2.Any) else pack(msg)


def _type_name(type_url: str) -> str:
    """The fully-qualified name a type URL names: everything after its last
    ``/``, whatever the prefix."""
    return type_url.rsplit("/", 1)[-1]


@dataclass(frozen=True)
class FactRecord:
    """What a fact handler records for one fact: the fact itself (as
    received, or annotated) followed by the events that flag it. The fact
    and each flag are a message (packed with the framework type-URL prefix)
    or an Any. Each flag is recorded with no header and the fact's
    created_at, and folds into the state the next fact sees."""

    fact: object
    flags: tuple = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "flags", tuple(self.flags))

    @classmethod
    def as_received(cls, fact) -> FactRecord:
        """The record of ``fact`` as received, with no flags."""
        return cls(fact)

    def to_abi(self) -> abi_pb2.FactRecord:
        """The ABI FactRecord the fact callback replies with."""
        return abi_pb2.FactRecord(
            fact=_as_any(self.fact), flags=[_as_any(flag) for flag in self.flags]
        )


class Conflict:
    """A stale command's conflict under AGGREGATE_HANDLES (types.proto,
    ContextualCommand.conflict_nonce): the window of events the command did
    not see, and the nonce delivered with it. The handler settles the window
    by calling :meth:`resolve_conflicts` with exactly that nonce before it
    returns; otherwise the router discards its output and the command fails
    CONFLICT_UNRESOLVED (ABORTED)."""

    __slots__ = ("_resolved", "nonce", "window")

    def __init__(self, window: list, nonce: bytes):
        self.window = list(window)
        self.nonce = bytes(nonce)
        self._resolved: bytes | None = None

    def resolve_conflicts(self, window, nonce: bytes) -> None:
        """Declare the window settled (the spec's
        ``resolve_conflicts(window, nonce)``). Only ``nonce`` is checked, by
        the router: it must be the delivered one."""
        del window
        self._resolved = bytes(nonce)

    @property
    def resolved_nonce(self) -> bytes:
        """The nonce the handler resolved with; empty when it did not."""
        return self._resolved or b""

    def __repr__(self) -> str:
        return f"Conflict(window={len(self.window)} pages, nonce={self.nonce!r})"


@dataclass
class CommandContext:
    """The historical-state evidence a handler sees, the cover it is
    handling, and the command's resolved Handling. ``cover`` is the
    command's cover: the aggregate's own domain and root, None when the
    command carried none. ``expected_sequence`` and ``conflict_resolution``
    are the resolved ``Handling`` (types.proto); ``conflict`` is set only
    under AGGREGATE_HANDLES (under STRICT there is no window, and under
    COMMUTATIVE the router settles it). ``command_id``, ``correlation_id``
    and ``fact_origin`` are the input's identity as its ContextualCommand's
    page delivered it, None when the page carries none, as the router's
    CommandContext has them."""

    next_sequence: int = 0
    had_prior_events: bool = False
    cover: types_pb2.Cover | None = None
    expected_sequence: int = 0
    conflict_resolution: int = types_pb2.CONFLICT_RESOLUTION_UNSPECIFIED
    conflict: Conflict | None = None
    # The input's identity, as delivered in its ContextualCommand's page:
    # its command id, its correlation and, for a fact, its origin.
    command_id: types_pb2.UUID | None = None
    correlation_id: types_pb2.UUID | None = None
    fact_origin: types_pb2.FactOrigin | None = None


@dataclass(frozen=True)
class PageContext:
    """Where the event or command a handler is handling sits: the cover of
    its book (None when the book carries none) and the page's explicit
    sequence (0 when the page carries none, and for commands and
    process-manager triggers, whose callbacks carry no page sequence).
    Projector folds and aggregate / process-manager appliers see the
    folded event's own book cover and page sequence; a saga handler sees the
    source book's cover and the triggering event's sequence."""

    cover: types_pb2.Cover | None = None
    sequence: int = 0


_CURRENT_PAGE: ContextVar[PageContext | None] = ContextVar(
    "angzarr_router_ffi_current_page", default=None
)


_CURRENT_INPUT: ContextVar[types_pb2.CommandPage | None] = ContextVar(
    "angzarr_router_ffi_current_input", default=None
)


@contextmanager
def _delivering(page: types_pb2.CommandPage) -> Iterator[None]:
    """Make ``page`` the input being dispatched while the block runs: what a
    command handler's CommandContext reads its identity from (the C ABI's
    CommandContextAux carries no identity; the ContextualCommand does)."""
    token = _CURRENT_INPUT.set(page)
    try:
        yield
    finally:
        _CURRENT_INPUT.reset(token)


@contextmanager
def _handling(ctx: PageContext) -> Iterator[None]:
    """Make ``ctx`` the current page context while the block runs."""
    token = _CURRENT_PAGE.set(ctx)
    try:
        yield
    finally:
        _CURRENT_PAGE.reset(token)


def _present(msg, name: str):
    """``msg``'s message field ``name``, or None when it is unset."""
    return getattr(msg, name) if msg.HasField(name) else None


def _cover_of(msg, name: str) -> types_pb2.Cover | None:
    """``msg``'s cover field ``name``, or None when it is unset."""
    return getattr(msg, name) if msg.HasField(name) else None


def current_page() -> PageContext:
    """The page context of the handler running on this thread. A dispatch
    sets it for every callback it makes: an aggregate command or fact
    handler sees its command's cover, a saga handler the source cover and the
    triggering event's sequence, a process-manager handler the trigger cover,
    a projector fold or an aggregate / process-manager applier its event's
    book cover and page sequence.
    Raises RuntimeError outside a dispatch."""
    ctx = _CURRENT_PAGE.get()
    if ctx is None:
        raise RuntimeError("no angzarr dispatch is being handled on this thread")
    return ctx


def current_cover() -> types_pb2.Cover:
    """The cover of the book the running handler is handling (see
    :func:`current_page`). Raises RuntimeError outside a dispatch or when that
    book carries no cover."""
    cover = current_page().cover
    if cover is None:
        raise RuntimeError("the book being handled carries no cover")
    return cover


def _command_context(cax) -> CommandContext:
    """The CommandContext a CommandContextAux stands for."""
    conflict = None
    if cax.conflict_resolution == types_pb2.CONFLICT_RESOLUTION_AGGREGATE_HANDLES:
        conflict = Conflict(cax.window, cax.conflict_nonce)
    page = _CURRENT_INPUT.get() or types_pb2.CommandPage()
    return CommandContext(
        next_sequence=cax.next_sequence,
        had_prior_events=cax.had_prior_events,
        cover=_cover_of(cax, "cover"),
        expected_sequence=cax.expected_sequence,
        conflict_resolution=cax.conflict_resolution,
        conflict=conflict,
        command_id=_present(page, "command_id"),
        correlation_id=_present(page, "correlation_id"),
        fact_origin=_present(page, "fact_origin"),
    )


# Thunk shapes (host-supplied business logic). ``state`` is a fresh instance
# of the component's state message, decoded from the router-owned state:
#   applier:   (state, payload: Any) -> None            (folds into state)
#   context applier: (state, payload: Any, ctx: PageContext) -> None
#   command:   (cmd: Any, state, cctx) -> EventBook|None (raises CodedError to reject)
#   fact:      (fact: Any, state) -> FactRecord          (the fact to record
#                                                         and its flags)
#   upcaster:  (payload: Any) -> message | Any | None    (None: unchanged)
ApplierThunk = Callable[[object, any_pb2.Any], None]
ApplierContextThunk = Callable[[object, any_pb2.Any, PageContext], None]
CommandThunk = Callable[[any_pb2.Any, object, CommandContext], object | None]
FactThunk = Callable[[any_pb2.Any, object], "FactRecord"]
UpcasterThunk = Callable[[any_pb2.Any], object | None]
#   changed:   (before, after) -> list[str]             (the conflict units
#                                                         whose values differ)
ChangedThunk = Callable[[object, object], list]


def _state_type(factory: Callable[[], object], component: str) -> str:
    """The fully-qualified name of the message ``factory`` builds: the state
    type the router holds the component's state as. A state that is not a
    protobuf message cannot cross the ABI."""
    state = factory()
    if not isinstance(state, Message):
        raise TypeError(
            f"{component}: state must be a protobuf message, "
            f"got {type(state).__name__}"
        )
    return state.DESCRIPTOR.full_name


@dataclass
class Rebuilder:
    """How a component's prior events fold into its state: the state
    message's factory (its default instance) and one applier per
    fully-qualified event type. The router holds the state; the binding folds
    each batch the router sends through these appliers."""

    factory: Callable[[], object]
    appliers: dict[str, ApplierThunk] = field(default_factory=dict)

    def apply(self, full_name: str, thunk: ApplierThunk) -> Rebuilder:
        """Register an applier for one fully-qualified event type."""
        self.appliers[full_name] = thunk
        return self

    def apply_with_context(
        self, full_name: str, thunk: ApplierContextThunk
    ) -> Rebuilder:
        """Register an applier for one fully-qualified event type that also
        receives the event's :class:`PageContext` (its book's cover and the
        page's sequence)."""

        def fold(state, event):
            thunk(state, event, current_page())

        self.appliers[full_name] = fold
        return self


@dataclass
class AggregateDispatch:
    """One aggregate component's registration: name, domain, rebuilder,
    command handlers, fact handlers, the command upcasters whose source types
    it claims, and the rules version it reports."""

    name: str
    domain: str
    rebuilder: Rebuilder
    commands: dict[str, CommandThunk] = field(default_factory=dict)
    facts: dict[str, FactThunk] = field(default_factory=dict)
    upcasters: dict[str, UpcasterThunk] = field(default_factory=dict)
    rules_version: str = ""
    changed: ChangedThunk | None = None

    def on_command(self, full_name: str, thunk: CommandThunk) -> AggregateDispatch:
        """Register a handler for one fully-qualified command type."""
        self.commands[full_name] = thunk
        return self

    def on_fact(self, fq_fact: str, thunk: FactThunk) -> AggregateDispatch:
        """Register the fact handler for a fully-qualified fact (event) type.
        The handler receives (fact Any, rebuilt state) and returns the
        :class:`FactRecord` to record (``FactRecord.as_received(fact)`` keeps
        the fact as received, with no flags). A fact cannot be refused; the
        core refuses a fact of an undeclared type with NO_FACT_HANDLER
        (INVALID_ARGUMENT) before any handler runs."""
        self.facts[fq_fact] = thunk
        return self

    def with_command_upcaster(
        self, source_type: str, thunk: UpcasterThunk
    ) -> AggregateDispatch:
        """Add an upcaster rule for incoming command and fact payloads of the
        fully-qualified ``source_type``: it receives the payload Any and
        returns the upcast payload (a message or an Any), or None to leave it
        unchanged. Rules run in registration order; the aggregate claims each
        source type at registration (a second claim of one type in its domain
        is DUPLICATE_COMMAND_HANDLER)."""
        self.upcasters[source_type] = thunk
        return self

    def with_commutative(self, changed: ChangedThunk) -> AggregateDispatch:
        """Turn on COMMUTATIVE conflict resolution (options.proto, the
        conflict_units region): ``changed(before, after)`` receives two
        decoded states and returns the conflict-unit paths whose values
        differ. The router runs the resolver and the overlap rule; the
        registration hello then lists COMMUTATIVE."""
        self.changed = changed
        return self

    def with_rules_version(self, version: str) -> AggregateDispatch:
        """Set the rules version reported on every response and in the
        registration hello: the business binary's full build version."""
        self.rules_version = version
        return self


# A projector domain that consumes every domain.
WILDCARD_DOMAIN = "*"


# Projector thunk shapes (``projection`` is a fresh instance of the projection
# message, decoded from the router-held projection):
#   event:   (projection, event: Any) -> None             (folds)
#   context event: (projection, event: Any, ctx: PageContext) -> None
ProjectorEventThunk = Callable[[object, any_pb2.Any], None]
ProjectorContextThunk = Callable[[object, any_pb2.Any, PageContext], None]


@dataclass
class ProjectorDispatch:
    """One projector component's registration: name, the projection
    message's factory, the domains it consumes (empty = all) and its fold
    handlers. The router holds the projection and answers the Projection
    itself (cover, projector name, next sequence)."""

    name: str
    factory: Callable[[], object]
    domains: list[str] = field(default_factory=list)
    events: dict[str, ProjectorEventThunk] = field(default_factory=dict)
    rules_version: str = ""

    def for_domains(self, *domains: str) -> ProjectorDispatch:
        """Restrict folding to books whose cover carries one of these domains.
        Unset (the default) or :data:`WILDCARD_DOMAIN` consumes every
        domain."""
        self.domains = list(domains)
        return self

    def on_event(self, full_name: str, thunk: ProjectorEventThunk) -> ProjectorDispatch:
        """Register the fold thunk for a fully-qualified event type name."""
        self.events[full_name] = thunk
        return self

    def on_event_with_context(
        self, full_name: str, thunk: ProjectorContextThunk
    ) -> ProjectorDispatch:
        """Register a fold thunk for a fully-qualified event type that also
        receives the event's :class:`PageContext` (its book's cover and the
        page's sequence)."""

        def fold(projection, event):
            thunk(projection, event, current_page())

        self.events[full_name] = fold
        return self

    def with_rules_version(self, version: str) -> ProjectorDispatch:
        """Set the rules version carried in the registration hello."""
        self.rules_version = version
        return self


# Saga thunk shapes (a saga is stateless — no state argument):
#   event: (event: Any, source_cover: Cover) -> commands
#   context event: (event: Any, source: PageContext) -> commands
# source_cover is the source book's cover passed through whole so the saga can
# route emitted commands by the trigger's identity; source adds the triggering
# event's sequence. Emitted commands pass through as emitted: the coordinator
# stamps their identity and provenance.
SagaEventThunk = Callable[[any_pb2.Any, object], list]
SagaContextThunk = Callable[[any_pb2.Any, PageContext], list]


@dataclass
class SagaDispatch:
    """One saga component's registration: name, the input domain it consumes,
    its one destination (``output_domain``, the domain every command it emits
    is for), and event handlers. A saga is stateless — no rebuilder, no
    state. A command for any other domain fails the saga's answer INTERNAL
    (SAGA_COMMAND_OUTSIDE_DESTINATION)."""

    name: str
    input_domain: str
    output_domain: str
    events: dict[str, SagaEventThunk] = field(default_factory=dict)
    rules_version: str = ""

    def on_event(self, full_name: str, thunk: SagaEventThunk) -> SagaDispatch:
        """Register the translation thunk for a fully-qualified event type."""
        self.events[full_name] = thunk
        return self

    def on_event_with_context(
        self, full_name: str, thunk: SagaContextThunk
    ) -> SagaDispatch:
        """Register a translation thunk for a fully-qualified event type that
        receives the triggering event's :class:`PageContext` (the source
        book's cover and the event's sequence) in place of the source
        cover."""

        def translate(event, _source_cover):
            return thunk(event, current_page())

        self.events[full_name] = translate
        return self

    def with_rules_version(self, version: str) -> SagaDispatch:
        """Set the rules version carried in the registration hello."""
        self.rules_version = version
        return self


# Process-manager thunk shapes (a PM is stateful — it sees its rebuilt state):
#   event:     (event: Any, state) -> ProcessManagerHandleResponse | None
#   cover event: (event: Any, state, trigger_cover: Cover | None) ->
#                ProcessManagerHandleResponse | None
PMEventThunk = Callable[[any_pb2.Any, object], object]
PMCoverEventThunk = Callable[[any_pb2.Any, object, object], object]


@dataclass
class ProcessManagerDispatch:
    """One process-manager component's registration, named as
    ComponentOptions names it: ``name``; ``domain``, its own workflow
    stream; the rebuilder for its event-sourced state; the domains it issues
    commands to, ``output_domain`` and/or ``output_domains`` (combined in
    :attr:`outputs`); and event handlers keyed by (trigger domain, FQ event
    type) — each handled event names the domain it triggers from."""

    name: str
    domain: str
    rebuilder: Rebuilder
    output_domain: str = ""
    output_domains: list[str] = field(default_factory=list)
    handlers: dict[str, dict[str, PMEventThunk]] = field(default_factory=dict)
    rules_version: str = ""

    @property
    def outputs(self) -> list[str]:
        """The domains the PM issues commands to: ``output_domain`` (when
        set) first, then ``output_domains``, deduplicated. An empty
        ``output_domains`` entry raises ValueError."""
        if any(not d for d in self.output_domains):
            raise ValueError(f"{self.name}: output_domains has an empty entry")
        combined = [self.output_domain] if self.output_domain else []
        return list(dict.fromkeys(combined + list(self.output_domains)))

    def on_event(
        self, domain: str, full_name: str, thunk: PMEventThunk
    ) -> ProcessManagerDispatch:
        """Register the thunk for an event of the fully-qualified type
        triggering from ``domain``."""
        self.handlers.setdefault(domain, {})[full_name] = thunk
        return self

    def on_event_with_cover(
        self, domain: str, full_name: str, thunk: PMCoverEventThunk
    ) -> ProcessManagerDispatch:
        """Register a thunk for an event of the fully-qualified type
        triggering from ``domain`` that also receives the trigger book's
        cover (None when it carries none)."""

        def handle(event, state):
            return thunk(event, state, current_page().cover)

        self.handlers.setdefault(domain, {})[full_name] = handle
        return self

    def with_rules_version(self, version: str) -> ProcessManagerDispatch:
        """Set the rules version reported on every response and in the
        registration hello: the business binary's full build version."""
        self.rules_version = version
        return self


# --- error model: CodedError <-> google.rpc.Status bytes ---


def _build_status_bytes(
    grpc: int, message: str, code: str, extras: dict | None
) -> bytes:
    """Serialize a coded failure as google.rpc.Status bytes carrying an
    ErrorInfo detail — the exact shape the core decodes (and gRPC puts on the
    wire). ErrorInfo Any uses the type.googleapis.com prefix the ABI pins."""
    info = error_details_pb2.ErrorInfo(
        reason=code, domain=ERROR_INFO_DOMAIN, metadata=extras or {}
    )
    any_info = any_pb2.Any()
    any_info.Pack(info)
    status = status_pb2.Status(code=int(grpc), message=message, details=[any_info])
    return status.SerializeToString()


def _error_status(exc: BaseException) -> tuple[bytes, int]:
    """Map a handler exception to (Status bytes, negative gRPC code): a
    CodedError keeps its code; any other exception is an unclassified failure
    → UNHANDLED_HANDLER_ERROR."""
    if isinstance(exc, CodedError):
        grpc = exc.grpc or GrpcCode.INVALID_ARGUMENT
        return _build_status_bytes(grpc, exc.message, exc.code, exc.extras), -int(grpc)
    return (
        _build_status_bytes(
            GrpcCode.INTERNAL, str(exc), CODE_UNHANDLED_HANDLER_ERROR, None
        ),
        -int(GrpcCode.INTERNAL),
    )


def _decode_status(data: bytes | None, ret: int) -> CodedError:
    """Turn google.rpc.Status bytes (with an ErrorInfo detail) back into a
    CodedError. ``ret`` (the negative callback/dispatch return) is the gRPC
    fallback when the bytes are absent or undecodable."""
    code = ""
    message = ""
    grpc = -ret
    extras: dict[str, str] = {}
    if data:
        status = status_pb2.Status()
        try:
            status.ParseFromString(data)
        except DecodeError:
            return CodedError(grpc=grpc)
        message = status.message
        if status.code != 0:
            grpc = status.code
        for detail in status.details:
            if detail.Is(error_details_pb2.ErrorInfo.DESCRIPTOR):
                info = error_details_pb2.ErrorInfo()
                detail.Unpack(info)
                code = info.reason
                extras = dict(info.metadata)
                break
    return CodedError(code=code, message=message, grpc=grpc, extras=extras)


# --- type-erased invokers ---

# An invoker bridges a callback_id to a registered typed thunk: it receives
# the marshaled inputs and returns (out_bytes, status). Thunk exceptions are
# NOT caught here (except by the fold, which answers a FoldFailure) — the
# trampoline catches them once.
Invoker = Callable[[str, bytes, bytes], tuple[bytes | None, int]]

# The status a failing fold answers with; `out` carries a FoldFailure.
_FOLD_FAILED = -int(GrpcCode.INTERNAL)


def _decode_state(factory: Callable[[], object], state: any_pb2.Any):
    """A fresh instance of the component's state message holding the
    router-owned ``state``."""
    msg = factory()
    msg.ParseFromString(state.value)
    return msg


def _fold_failure(message: str, page_index: int | None = None) -> bytes:
    failure = abi_pb2.FoldFailure(message=message)
    if page_index is not None:
        failure.page_index = page_index
    return failure.SerializeToString()


def _fold_invoker(factory, appliers: dict[str, ApplierThunk]) -> Invoker:
    """The one batched fold over router-owned state (a FoldCall): decode the
    state, apply every page whose type has an applier (others are skipped),
    and answer the re-encoded state. A failing applier answers a FoldFailure
    naming the failing page's index in the batch; the router never calls the
    fold again."""

    def inv(_type_url, payload, _aux):
        try:
            call = abi_pb2.FoldCall()
            call.ParseFromString(payload)
            state = _decode_state(factory, call.state)
        except BaseException as exc:  # noqa: BLE001 — answered as a FoldFailure
            return _fold_failure(str(exc)), _FOLD_FAILED
        cover = _cover_of(call, "cover")
        applied = False
        for index, page in enumerate(call.pages):
            if not page.HasField("payload"):
                continue
            thunk = appliers.get(_type_name(page.payload.type_url))
            if thunk is None:
                continue
            try:
                with _handling(PageContext(cover=cover, sequence=page.sequence)):
                    thunk(state, page.payload)
            except BaseException as exc:  # noqa: BLE001 — answered as a FoldFailure
                return _fold_failure(str(exc), index), _FOLD_FAILED
            applied = True
        if not applied:
            return None, _STATUS_OK_EMPTY
        return pack(state).SerializeToString(), _STATUS_OK

    return inv


def _command_invoker(factory, thunk: CommandThunk) -> Invoker:
    def inv(type_url, payload, aux):
        cax = abi_pb2.CommandContextAux()
        cax.ParseFromString(aux)
        cctx = _command_context(cax)
        state = _decode_state(factory, cax.state)
        with _handling(PageContext(cover=cctx.cover)):
            book = thunk(any_pb2.Any(type_url=type_url, value=payload), state, cctx)
        resolved = cctx.conflict.resolved_nonce if cctx.conflict else b""
        if book is None and not resolved:
            return None, _STATUS_OK_EMPTY
        output = abi_pb2.CommandOutput(resolved_nonce=resolved)
        if book is not None:
            output.events.CopyFrom(book)
        return output.SerializeToString(), _STATUS_OK

    return inv


def _fact_invoker(factory, thunk: FactThunk) -> Invoker:
    # The fact's cover is the dispatch-level page context Router.dispatch
    # sets; the aux carries only the rebuilt state.
    def inv(type_url, payload, aux):
        cax = abi_pb2.CommandContextAux()
        cax.ParseFromString(aux)
        state = _decode_state(factory, cax.state)
        record = thunk(any_pb2.Any(type_url=type_url, value=payload), state)
        if not isinstance(record, FactRecord):
            raise TypeError(
                f"fact handler for {type_url!r} returned {type(record).__name__}, not a FactRecord"
            )
        return record.to_abi().SerializeToString(), _STATUS_OK

    return inv


def _changed_invoker(factory, changed: ChangedThunk) -> Invoker:
    def inv(_type_url, payload, _aux):
        call = abi_pb2.ChangedCall()
        call.ParseFromString(payload)
        before = _decode_state(factory, call.before)
        after = _decode_state(factory, call.after)
        units = abi_pb2.ChangedUnits(units=list(changed(before, after)))
        return units.SerializeToString(), _STATUS_OK

    return inv


def _upcaster_invoker(thunk: UpcasterThunk) -> Invoker:
    def inv(type_url, payload, _aux):
        upcast = thunk(any_pb2.Any(type_url=type_url, value=payload))
        if upcast is None:
            return None, _STATUS_OK_EMPTY
        return _as_any(upcast).SerializeToString(), _STATUS_OK

    return inv


def _saga_event_invoker(thunk: SagaEventThunk) -> Invoker:
    # Saga is stateless. The event thunk returns the commands of a
    # SagaResponse.
    def inv(type_url, payload, aux):
        sax = abi_pb2.SagaEventAux()
        sax.ParseFromString(aux)
        source = PageContext(
            cover=_cover_of(sax, "source_cover"), sequence=sax.source_seq
        )
        with _handling(source):
            commands = thunk(
                any_pb2.Any(type_url=type_url, value=payload),
                sax.source_cover,
            )
        resp = saga_pb2.SagaResponse(commands=commands or [])
        return resp.SerializeToString(), _STATUS_OK

    return inv


def _pm_event_invoker(factory, thunk: PMEventThunk) -> Invoker:
    # The PM is stateful: the aux carries its router-rebuilt state and the
    # trigger cover, which is the handler's page context. The host returns a
    # full ProcessManagerHandleResponse.
    def inv(type_url, payload, aux):
        pax = abi_pb2.PmEventAux()
        pax.ParseFromString(aux)
        state = _decode_state(factory, pax.state)
        with _handling(PageContext(cover=_cover_of(pax, "trigger_cover"))):
            resp = thunk(any_pb2.Any(type_url=type_url, value=payload), state)
        if resp is None:
            return None, _STATUS_OK_EMPTY
        return resp.SerializeToString(), _STATUS_OK

    return inv


# --- the callback table and the single cffi callback trampoline ---

# Every registered invoker, keyed by the callback id its registration
# assigned. A router hosts one component, but a process may hold several
# Router objects at once (one closed and another opened, or several in
# tests); the C callback carries no session, so the table is module-wide and
# ids are unique across routers: the id alone selects the invoker.
_CALLBACKS: dict[int, Invoker] = {}
_CALLBACK_IDS = itertools.count(1)


def _c_bytes(ptr, n) -> bytes:
    """Copy a router-owned input buffer (valid only for this callback) into
    Python bytes."""
    if ptr == ffi.NULL or n == 0:
        return b""
    return bytes(ffi.buffer(ptr, n))


def _write_out(out, data: bytes | None) -> None:
    """Fill a router-allocated out buffer (host allocates via
    angzarr_buf_alloc; the router consumes and frees it). Empty leaves
    out null/zero."""
    if out == ffi.NULL:
        return
    if not data:
        out.data = ffi.NULL
        out.len = 0
        return
    ptr = lib.angzarr_buf_alloc(len(data))
    ffi.memmove(ptr, data, len(data))
    out.data = ptr
    out.len = len(data)


# What cffi returns if the trampoline itself fails to return a value: an
# INTERNAL status, never STATUS_OK with `out` unset.
_TRAMPOLINE_ERROR = -int(GrpcCode.INTERNAL)


@ffi.callback("angzarr_cb", error=_TRAMPOLINE_ERROR)
def _trampoline(
    callback_id,
    type_url,
    type_url_len,
    payload,
    payload_len,
    aux,
    aux_len,
    out,
):
    """The single C-visible gateway the core calls for every host callback.
    Routes by callback_id to the registered invoker and writes the response
    into out. Any raised BaseException is caught and surfaced as a coded
    failure — nothing unwinds across the boundary into Rust."""
    try:
        inv = _CALLBACKS.get(int(callback_id))
        if inv is None:
            data, code = _error_status(
                CodedError(
                    code=CODE_UNHANDLED_HANDLER_ERROR,
                    message=f"no host callback registered for id {int(callback_id)}",
                    grpc=GrpcCode.INTERNAL,
                )
            )
            _write_out(out, data)
            return code
        data, status = inv(
            _c_bytes(type_url, type_url_len).decode("utf-8"),
            _c_bytes(payload, payload_len),
            _c_bytes(aux, aux_len),
        )
        _write_out(out, data)
        return status
    # Every Python exception (KeyboardInterrupt included) becomes a status:
    # none may unwind into the Rust caller.
    except BaseException as exc:  # noqa: BLE001
        data, code = _error_status(exc)
        _write_out(out, data)
        return code


def _as_u8(buf: bytes):
    """A read-only uint8_t* view over Python bytes (no copy). The caller must
    keep ``buf`` alive for the duration of the C call."""
    if not buf:
        return ffi.NULL
    return ffi.cast("uint8_t*", ffi.from_buffer(buf))


def _consume_out(out) -> bytes:
    """Copy a router-allocated out buffer into Python bytes and release it
    (the dispatch out is router-owned)."""
    if out.data == ffi.NULL or out.len == 0:
        return b""
    data = bytes(ffi.buffer(out.data, out.len))
    lib.angzarr_buf_release(out.data, out.len)
    out.data = ffi.NULL
    out.len = 0
    return data


class Router:
    """Wraps the Rust core router hosting ONE component (an aggregate, saga,
    process manager or projector, with its upcasters) and the callbacks its
    registration assigned. Registration is not safe for concurrent use;
    concurrent dispatch is — every callback is stateless and the router owns all
    state."""

    def __init__(self):
        self._ptr = lib.angzarr_router_new()
        self._callback_ids: list[int] = []
        # (kind, name) of the one component this router hosts, once registered.
        self._component: tuple[str, str] | None = None
        self._lock = threading.Lock()

    def close(self) -> None:
        """Free the underlying Rust router and drop its callbacks. Safe to
        call more than once."""
        if self._ptr is not None:
            lib.angzarr_router_free(self._ptr)
            self._ptr = None
            for cid in self._callback_ids:
                _CALLBACKS.pop(cid, None)
            self._callback_ids.clear()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def _assign(self, inv: Invoker) -> int:
        cid = next(_CALLBACK_IDS)
        _CALLBACKS[cid] = inv
        self._callback_ids.append(cid)
        return cid

    def _call(self, fn, request: bytes, page: PageContext) -> tuple[int, bytes]:
        """Run one dispatch entry point over ``request`` bytes with ``page``
        as the dispatch-level page context (callbacks whose aux carries a
        cover narrow it). Returns the dispatch's return code and its consumed
        out bytes."""
        out = ffi.new("angzarr_buf*")
        with _handling(page):
            ret = fn(self._ptr, _as_u8(request), len(request), out)
        return ret, _consume_out(out)

    def _register(self, fn, desc, kind: str, first_callback: int) -> None:
        """Hand ``desc`` to the core. A refused registration drops the
        callbacks it assigned (from index ``first_callback``); a second
        component is refused DUPLICATE_REGISTRATION naming the one this
        router already hosts."""
        desc_bytes = desc.SerializeToString()
        ret = fn(self._ptr, _as_u8(desc_bytes), len(desc_bytes), _trampoline)
        if ret == 0:
            self._component = (kind, desc.name)
            return
        for cid in self._callback_ids[first_callback:]:
            _CALLBACKS.pop(cid, None)
        del self._callback_ids[first_callback:]
        if self._component is not None:
            held_kind, held_name = self._component
            raise CodedError(
                code=CODE_DUPLICATE_REGISTRATION,
                message=f"this router already hosts its one component, the "
                f"{held_kind} {held_name!r}",
                grpc=GrpcCode.INVALID_ARGUMENT,
                extras={"component": held_name, "kind": held_kind},
            )
        raise _decode_status(None, ret)

    def register_aggregate(self, dispatch: AggregateDispatch) -> None:
        """Register one aggregate component: assign callback ids to every
        thunk, serialize the AggregateDescriptor (its state type, rules
        version and upcaster source types included), and hand it to the core
        with the shared trampoline."""
        with self._lock:
            first = len(self._callback_ids)
            factory = dispatch.rebuilder.factory
            desc = abi_pb2.AggregateDescriptor(
                name=dispatch.name,
                domain=dispatch.domain,
                rules_version=dispatch.rules_version,
                state_type_url=_state_type(factory, dispatch.name),
            )
            if dispatch.rebuilder.appliers:
                desc.fold_callback_id = self._assign(
                    _fold_invoker(factory, dict(dispatch.rebuilder.appliers))
                )
            for fq, thunk in dispatch.commands.items():
                cid = self._assign(_command_invoker(factory, thunk))
                desc.commands.append(abi_pb2.CallbackEntry(fq_type=fq, callback_id=cid))
            for fq, thunk in dispatch.facts.items():
                cid = self._assign(_fact_invoker(factory, thunk))
                desc.facts.append(abi_pb2.CallbackEntry(fq_type=fq, callback_id=cid))
            if dispatch.changed is not None:
                desc.changed_callback_id = self._assign(
                    _changed_invoker(factory, dispatch.changed)
                )
            for fq, thunk in dispatch.upcasters.items():
                cid = self._assign(_upcaster_invoker(thunk))
                desc.command_upcasters.append(
                    abi_pb2.CallbackEntry(fq_type=fq, callback_id=cid)
                )
            self._register(
                lib.angzarr_router_register_aggregate, desc, "aggregate", first
            )

    def dispatch(self, contextual_command) -> object:
        """Run one ContextualCommand (a command, or a fact in the page's Fact
        arm) through the core and return the CommandHandlerResponse, or raise
        a CodedError decoded from the core's failure."""
        with _delivering(contextual_command.command.page):
            ret, resp_bytes = self._call(
                lib.angzarr_router_dispatch,
                contextual_command.SerializeToString(),
                PageContext(cover=_cover_of(contextual_command.command, "cover")),
            )
        if ret == 0:
            resp = command_handler_pb2.CommandHandlerResponse()
            if resp_bytes:
                resp.ParseFromString(resp_bytes)
            return resp
        raise _decode_status(resp_bytes, ret)

    def register_projector(self, dispatch: ProjectorDispatch) -> None:
        """Register one projector component: assign the fold's callback id,
        serialize the ProjectorDescriptor (its projection type and rules
        version included), and hand it to the core with the shared
        trampoline."""
        with self._lock:
            first = len(self._callback_ids)
            desc = abi_pb2.ProjectorDescriptor(
                name=dispatch.name,
                domains=dispatch.domains,
                projection_type_url=_state_type(dispatch.factory, dispatch.name),
                rules_version=dispatch.rules_version,
            )
            if dispatch.events:
                desc.fold_callback_id = self._assign(
                    _fold_invoker(dispatch.factory, dict(dispatch.events))
                )
            self._register(
                lib.angzarr_router_register_projector, desc, "projector", first
            )

    def dispatch_projector(self, event_book) -> object:
        """Fold one EventBook through the registered projector and return the
        Projection the router built, or raise a CodedError decoded from the
        core's failure."""
        ret, resp_bytes = self._call(
            lib.angzarr_router_dispatch_projector,
            event_book.SerializeToString(),
            PageContext(cover=_cover_of(event_book, "cover")),
        )
        if ret == 0:
            proj = types_pb2.Projection()
            if resp_bytes:
                proj.ParseFromString(resp_bytes)
            return proj
        raise _decode_status(resp_bytes, ret)

    def register_saga(self, dispatch: SagaDispatch) -> None:
        """Register one saga component: assign callback ids to every event
        thunk, serialize the SagaDescriptor, and hand it to the core with the
        shared trampoline."""
        with self._lock:
            first = len(self._callback_ids)
            desc = abi_pb2.SagaDescriptor(
                name=dispatch.name,
                input_domain=dispatch.input_domain,
                output_domain=dispatch.output_domain,
                rules_version=dispatch.rules_version,
            )
            for fq, thunk in dispatch.events.items():
                cid = self._assign(_saga_event_invoker(thunk))
                desc.events.append(abi_pb2.CallbackEntry(fq_type=fq, callback_id=cid))
            self._register(lib.angzarr_router_register_saga, desc, "saga", first)

    def dispatch_saga(self, source) -> object:
        """Run one source EventBook through the registered saga and return
        its SagaResponse, or raise a CodedError decoded from the core's
        failure."""
        ret, resp_bytes = self._call(
            lib.angzarr_router_dispatch_saga,
            source.SerializeToString(),
            PageContext(cover=_cover_of(source, "cover")),
        )
        if ret == 0:
            resp = saga_pb2.SagaResponse()
            if resp_bytes:
                resp.ParseFromString(resp_bytes)
            return resp
        raise _decode_status(resp_bytes, ret)

    def register_process_manager(self, dispatch: ProcessManagerDispatch) -> None:
        """Register one process-manager component: assign callback ids to its
        fold and event thunks, serialize the ProcessManagerDescriptor (its
        state type and rules version included), and hand it to the core with
        the shared trampoline."""
        with self._lock:
            first = len(self._callback_ids)
            factory = dispatch.rebuilder.factory
            desc = abi_pb2.ProcessManagerDescriptor(
                name=dispatch.name,
                pm_domain=dispatch.domain,
                target_domains=dispatch.outputs,
                rules_version=dispatch.rules_version,
                state_type_url=_state_type(factory, dispatch.name),
            )
            if dispatch.rebuilder.appliers:
                desc.fold_callback_id = self._assign(
                    _fold_invoker(factory, dict(dispatch.rebuilder.appliers))
                )
            for trigger_domain, by_type in dispatch.handlers.items():
                for fq, thunk in by_type.items():
                    cid = self._assign(_pm_event_invoker(factory, thunk))
                    desc.events.append(
                        abi_pb2.PmEventEntry(
                            input_domain=trigger_domain, fq_type=fq, callback_id=cid
                        )
                    )
            self._register(
                lib.angzarr_router_register_process_manager,
                desc,
                "process manager",
                first,
            )

    def dispatch_process_manager(self, pm_request) -> object:
        """Run one ProcessManagerHandleRequest through the registered process
        manager and return its ProcessManagerHandleResponse, or raise a
        CodedError decoded from the core's failure."""
        ret, resp_bytes = self._call(
            lib.angzarr_router_dispatch_process_manager,
            pm_request.SerializeToString(),
            PageContext(cover=_cover_of(pm_request.trigger, "cover")),
        )
        if ret == 0:
            resp = process_manager_pb2.ProcessManagerHandleResponse()
            if resp_bytes:
                resp.ParseFromString(resp_bytes)
            return resp
        raise _decode_status(resp_bytes, ret)

    def component_descriptor(self):
        """The registration hello of the registered component: the
        ``io.angzarr.v1.ComponentDescriptor`` the router built from its own
        table (name, kind, inputs, rules version and, for an aggregate or a
        process manager, the state type and its conflict resolutions), which
        ComponentService.Describe answers. Raises the router's CodedError
        when nothing is registered (NO_HANDLERS_REGISTERED) or the
        component cannot build one (MISSING_RULES_VERSION,
        MISSING_STATE_TYPE)."""
        out = ffi.new("angzarr_buf*")
        ret = lib.angzarr_router_component_descriptor(self._ptr, out)
        data = _consume_out(out)
        if ret != 0:
            raise _decode_status(data, ret)
        hello = types_pb2.ComponentDescriptor()
        hello.ParseFromString(data)
        return hello


def abi_version() -> int:
    """The ABI version the linked router-ffi exposes. Bindings check it so a
    binding and a router-ffi artifact that have drifted refuse each other."""
    return int(lib.angzarr_abi_version())
