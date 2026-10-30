"""What business code is handed: the context of the input it handles, the
place of the page it folds, a stale command's conflict, a fact's record;
and the fold every component shares (decision 14.40: the starting state,
then every page given, in order)."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from google.protobuf import any_pb2
from google.protobuf.message import DecodeError, Message

from ..proto.io.angzarr.v1 import types_pb2
from .errors import (
    PERSISTED_EVENT_CORRUPT,
    CodedError,
    any_decode_error,
    as_cause,
    escaped,
    pack,
    type_name,
)

# An applier, or a projector's fold: (state, event Any, its PageContext).
Applier = Callable[[object, any_pb2.Any, "PageContext"], None]


def as_any(msg) -> any_pb2.Any:
    """``msg`` itself when it is an Any, otherwise ``msg`` packed."""
    return msg if isinstance(msg, any_pb2.Any) else pack(msg)


def decode(message_type, data: bytes):
    """``data`` decoded as ``message_type``, or ANY_DECODE_FAILED."""
    msg = message_type()
    try:
        msg.ParseFromString(data)
    except DecodeError as exc:
        raise any_decode_error(message_type.DESCRIPTOR.full_name, exc) from None
    return msg


def _field(msg, name: str):
    return getattr(msg, name) if msg.HasField(name) else None


class Conflict:
    """A stale command's conflict under AGGREGATE_HANDLES: the window of
    pages the command did not see (from its expected sequence on) and the
    nonce delivered with it. The handler settles the window by calling
    :meth:`resolve_conflicts`; the router reports the nonce it was passed
    and the coordinator judges it (decision 14.42)."""

    __slots__ = ("_resolved", "nonce", "window")

    def __init__(self, window: Iterable, nonce: bytes):
        self.window = list(window)
        self.nonce = bytes(nonce)
        self._resolved = b""

    def resolve_conflicts(self, window, nonce: bytes) -> None:
        """Declare the window settled with ``nonce`` (decision 12.7's
        ``resolve_conflicts(window, nonce)``)."""
        del window
        self._resolved = bytes(nonce)

    @property
    def resolved_nonce(self) -> bytes:
        """The nonce the handler settled with; empty when it did not."""
        return self._resolved


@dataclass
class CommandContext:
    """What a command, fact or refusal handler is handed beside its input:
    the historical evidence (``next_sequence``, ``had_prior_events``), the
    cover it handles, the resolved Handling (``expected_sequence``,
    ``conflict_resolution``; ``conflict`` under AGGREGATE_HANDLES only), the
    input's identity as delivered, and when the input was accepted
    (``recorded_at``, decision 14.37)."""

    next_sequence: int = 0
    had_prior_events: bool = False
    cover: types_pb2.Cover | None = None
    expected_sequence: int = 0
    conflict_resolution: int = types_pb2.CONFLICT_RESOLUTION_UNSPECIFIED
    conflict: Conflict | None = None
    command_id: types_pb2.UUID | None = None
    correlation_id: types_pb2.UUID | None = None
    fact_origin: types_pb2.FactOrigin | None = None
    recorded_at: object | None = None


def book_context(book: types_pb2.CommandBook, next_sequence: int = 0) -> CommandContext:
    """The CommandContext of an input's CommandBook: its cover, resolved
    Handling (the expected sequence defaults to ``next_sequence``) and
    identity."""
    handling, page = book.handling, book.page
    expected = _field(handling, "expected_sequence")
    return CommandContext(
        next_sequence=next_sequence,
        cover=_field(book, "cover"),
        expected_sequence=next_sequence if expected is None else expected,
        conflict_resolution=handling.conflict_resolution,
        command_id=_field(page, "command_id"),
        correlation_id=_field(page, "correlation_id"),
        fact_origin=_field(page, "fact_origin"),
    )


@dataclass(frozen=True)
class FactRecord:
    """What a fact handler records for one fact: the fact itself (as
    received, or annotated), then the events that flag it. Each is a
    message or an Any."""

    fact: object
    flags: tuple = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "flags", tuple(self.flags))

    @classmethod
    def as_received(cls, fact) -> FactRecord:
        """The record of ``fact`` as received, with no flags."""
        return cls(fact)

    def book(self) -> types_pb2.EventBook:
        """The events this record records: the fact, then its flags."""
        payloads = [self.fact, *self.flags]
        return types_pb2.EventBook(
            pages=[types_pb2.EventPage(payload=as_any(p)) for p in payloads]
        )


@dataclass(frozen=True)
class PageContext:
    """Where the input a handler handles sits: its book's cover (None when
    it carries none), the page's sequence and meta. A command handler sees
    its command's cover; an applier or projector fold its event's book cover
    and sequence; a policy handler the source cover and its trigger's
    sequence."""

    cover: types_pb2.Cover | None = None
    sequence: int = 0
    meta: types_pb2.EventMeta | None = None


_CURRENT_PAGE: ContextVar[PageContext | None] = ContextVar(
    "angzarr_router_current_page", default=None
)


@contextmanager
def handling(ctx: PageContext) -> Iterator[None]:
    """Make ``ctx`` the running handler's page context."""
    token = _CURRENT_PAGE.set(ctx)
    try:
        yield
    finally:
        _CURRENT_PAGE.reset(token)


def current_page() -> PageContext:
    """The page context of the handler running in this context. Raises
    RuntimeError outside a dispatch."""
    ctx = _CURRENT_PAGE.get()
    if ctx is None:
        raise RuntimeError("no angzarr dispatch is being handled here")
    return ctx


def current_cover() -> types_pb2.Cover:
    """The cover of the book the running handler handles. Raises
    RuntimeError outside a dispatch or when that book carries no cover."""
    cover = current_page().cover
    if cover is None:
        raise RuntimeError("the book being handled carries no cover")
    return cover


def state_type_url(state: Message) -> str:
    return "/" + state.DESCRIPTOR.full_name


def start_state(factory: Callable[[], Message], book: types_pb2.EventBook) -> Message:
    """A fresh state, holding the delivered starting state when the book
    carries one (the snapshot the coordinator chose, decision 14.40)."""
    state = factory()
    if book.snapshot.HasField("state"):
        try:
            state.ParseFromString(book.snapshot.state.value)
        except DecodeError:
            raise CodedError(
                PERSISTED_EVENT_CORRUPT,
                extras={"type_url": book.snapshot.state.type_url},
            ) from None
    return state


def fold(state, pages, cover, appliers: dict[str, Applier], code: str) -> None:
    """Fold every page whose type has an applier into ``state``, in order;
    a failing applier fails the fold as ``code`` (FOLD_FAILED or
    FOLD_FAILED_EMITTED), naming the page and its cause."""
    for page in pages:
        applier = appliers.get(type_name(page.payload.type_url))
        if applier is None:
            continue
        ctx = PageContext(cover, page.sequence, _field(page, "meta"))
        try:
            with handling(ctx):
                applier(state, page.payload, ctx)
        except Exception as exc:  # noqa: BLE001 - the fold's cause, coded
            args = {
                "sequence": str(page.sequence),
                "type_url": page.payload.type_url,
                "state_type_url": state_type_url(state),
            }
            raise CodedError(code, extras={**args, **as_cause(exc)}) from None


def call(ctx: PageContext, handler: Callable, *args):
    """Run business code under ``ctx``, answering what escapes it coded."""
    try:
        with handling(ctx):
            return handler(*args)
    except Exception as exc:  # noqa: BLE001 - every escape is answered, coded
        raise escaped(exc) from None
