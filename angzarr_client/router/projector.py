"""A projector's dispatch: ``ProjectorService.Handle``. Every delivered event
of a consumed domain folds into one projection, which the answer carries."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from google.protobuf import any_pb2

from ..proto.io.angzarr.v1 import types_pb2
from .context import Applier, fold
from .errors import FOLD_FAILED, pack


@dataclass
class ProjectorDispatch:
    """One projector's dispatch table: its name, its projection message's
    factory, the domains it consumes, each named, and its fold handlers by
    fully-qualified event type. There is no wildcard domain (decision
    14.53): a book from a domain not listed, ``"*"`` among them, folds
    nothing. A table given no domains folds every book when the router is
    driven directly; a host refuses to register it, as describe() refuses
    to describe it (``HOST_REASON_PROJECTOR_NO_INPUT_DOMAIN``, decision
    14.55)."""

    name: str
    factory: Callable[[], object]
    domains: list[str] = field(default_factory=list)
    events: dict[str, Applier] = field(default_factory=dict)
    rules_version: str = ""

    kind = types_pb2.COMPONENT_KIND_PROJECTOR

    def for_domains(self, *domains: str) -> ProjectorDispatch:
        """Fold only books whose cover names one of ``domains``."""
        self.domains = list(domains)
        return self

    def on_event(
        self, full_name: str, thunk: Callable[[object, any_pb2.Any], None]
    ) -> ProjectorDispatch:
        """Register the fold ``(projection, event)`` of one event type."""
        self.events[full_name] = lambda projection, event, _ctx: thunk(
            projection, event
        )
        return self

    def on_event_with_context(
        self, full_name: str, thunk: Applier
    ) -> ProjectorDispatch:
        """Register the fold ``(projection, event, PageContext)`` of one
        event type: it also reads the event's book cover and sequence."""
        self.events[full_name] = thunk
        return self

    def with_rules_version(self, version: str) -> ProjectorDispatch:
        """Set the build version the projector describes itself with."""
        self.rules_version = version
        return self

    def handle(self, book: types_pb2.EventBook) -> types_pb2.Projection:
        """Fold the book (nothing when its domain is not consumed) and answer
        the Projection: the cover, the projector, the position after the
        book and the projection."""
        projection = self.factory()
        if not self.domains or book.cover.domain in self.domains:
            cover = book.cover if book.HasField("cover") else None
            fold(projection, book.pages, cover, self.events, FOLD_FAILED)
        after = book.pages[-1].sequence + 1 if book.pages else book.next_sequence
        return types_pb2.Projection(
            cover=book.cover,
            projector=self.name,
            next_sequence=after,
            projection=pack(projection),
        )
