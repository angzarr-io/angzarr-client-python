"""Reusable fakes for cucumber step files that exercise real production
clients without opening sockets.

The client-surface tier (``features/client/README.md``) runs the client
objects against the **test backend**: :class:`TestBackend` below, an
in-process fake of the coordinator gRPC services reached through
:class:`FakeChannel`. The remaining pieces are lower-level doubles:

  - :class:`RecordingStub` — a duck-typed stand-in for any of the gRPC
    service stubs (`CommandHandlerCoordinatorServiceStub`,
    `EventQueryServiceStub`, etc.). Records every method call and
    returns canned responses (or raises canned errors) keyed by method
    name. Plug into the production wrapping clients via the new
    `from_stub` / `from_stubs` factories on
    `CommandHandlerClient`, `QueryClient`, `SpeculativeClient`.

  - :class:`StubRpcError` — a minimal `grpc.RpcError`-quacked exception
    that the wrapping clients can catch and wrap in the production
    `GRPCError` class. Code/details are required so error-classification
    assertions work end-to-end.

PARITY_AUDIT.md plan item P1.12.e / finding #24.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import grpc
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
from google.protobuf.any_pb2 import Any as ProtoAny
from google.protobuf.message import DecodeError
from google.protobuf.timestamp_pb2 import Timestamp

from angzarr_client import client as client_module
from angzarr_client._pb import (
    CommandBook,
    CommandPage,
    CommandRequest,
    CommandResponse,
    Cover,
    Edition,
    EventBook,
    EventPage,
    ProcessManagerHandleResponse,
    Projection,
    Query,
    SagaResponse,
    Snapshot,
    SpeculateCommandHandlerRequest,
    SpeculatePmRequest,
    SpeculateProjectorRequest,
    SpeculateSagaRequest,
    SyncMode,
)
from angzarr_client.helpers import DEFAULT_EDITION, TYPE_URL_PREFIX


class StubRpcError(grpc.RpcError):
    """Minimal `grpc.RpcError` carrying a code() and details().

    The production code paths (`QueryClient.get_event_book`,
    `CommandHandlerClient.handle_command`, etc.) catch
    `grpc.RpcError` and wrap it in `GRPCError(e)`. So this stand-in
    must inherit from `grpc.RpcError` and implement the same surface
    (`code()` and `details()`) as the real exception.
    """

    def __init__(self, code: grpc.StatusCode, details: str) -> None:
        super().__init__(details)
        self._code = code
        self._details = details

    def code(self) -> grpc.StatusCode:
        return self._code

    def details(self) -> str:
        return self._details

    def __str__(self) -> str:
        return self._details


# A canned response can be either a static value or a callable
# (request, **kwargs) -> response, for cases where the response depends
# on what was sent.
CannedResponse = Any | Callable[..., Any]


class RecordingStub:
    """Duck-typed gRPC stub. Records every call; returns canned values.

    Usage::

        stub = RecordingStub()
        stub.responses["GetEventBook"] = EventBook(...)
        stub.errors["HandleCommand"] = StubRpcError(
            grpc.StatusCode.FAILED_PRECONDITION, "stale sequence"
        )
        client = QueryClient.from_stub(stub)
        client.get_event_book(query)
        # → stub.calls == [("GetEventBook", query, {"timeout": None})]

    Methods not pre-registered return ``None`` (the wrapping client
    will then surface a TypeError; explicitly register every method
    you intend to call).
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any, dict[str, Any]]] = []
        self.responses: dict[str, CannedResponse] = {}
        self.errors: dict[str, BaseException] = {}

    def __getattr__(self, name: str) -> Callable[..., Any]:
        # __getattr__ fires only for unset attributes; so once a name
        # is queried, generate a callable that records + dispatches.
        # No need to cache: each access through gRPC stubs is a
        # one-shot method-name lookup.
        def _caller(request: Any = None, /, **kwargs: Any) -> Any:
            self.calls.append((name, request, dict(kwargs)))
            if name in self.errors:
                raise self.errors[name]
            r = self.responses.get(name)
            if callable(r):
                return r(request, **kwargs)
            return r

        return _caller

    def last_call(self, method: str) -> tuple[Any, dict[str, Any]] | None:
        """Return the (request, kwargs) of the last call to ``method``,
        or ``None`` if it was never called."""
        for n, req, kwargs in reversed(self.calls):
            if n == method:
                return (req, kwargs)
        return None

    def call_count(self, method: str) -> int:
        """How many times ``method`` was invoked."""
        return sum(1 for n, _, _ in self.calls if n == method)


# ===========================================================================
# Test backend: in-process fake of the coordinator services
# ===========================================================================
#
# The backend keeps an in-memory event store and a tiny scripted aggregate:
#
# - commands are ``order.GenericCommand`` payloads (or ``order.CreateOrder``);
#   a command named ``XxxYyy`` emits ``count`` events (default 1) named by
#   :func:`event_name_for`;
# - ``CreateOrder`` requires ``customer_id``;
# - ``CancelOrder`` is refused once the history holds an ``OrderShipped``;
# - every page carrying an explicit sequence must name the aggregate's next
#   sequence (optimistic concurrency, FAILED_PRECONDITION otherwise).
#
# Sync modes: ``ASYNC`` returns immediately and queues the downstream work
# (run it with :meth:`TestBackend.drain`), ``SIMPLE`` runs the configured
# projectors before replying, ``CASCADE`` also runs the configured sagas
# (which write to their target aggregate) before replying.

ORDER_PACKAGE = "order"


def _build_order_messages() -> dict[str, type]:
    fdp = descriptor_pb2.FileDescriptorProto(
        name="angzarr_test_backend/order.proto",
        package=ORDER_PACKAGE,
        syntax="proto3",
    )
    F = descriptor_pb2.FieldDescriptorProto

    def msg(name: str, *fields: tuple[str, int, int, int]) -> None:
        m = fdp.message_type.add(name=name)
        for fname, number, ftype, label in fields:
            m.field.add(name=fname, number=number, type=ftype, label=label)

    opt, rep = F.LABEL_OPTIONAL, F.LABEL_REPEATED
    msg(
        "CreateOrder",
        ("order_id", 1, F.TYPE_STRING, opt),
        ("customer_id", 2, F.TYPE_STRING, opt),
        ("items", 3, F.TYPE_STRING, rep),
    )
    msg(
        "GenericCommand",
        ("data", 1, F.TYPE_STRING, opt),
        ("count", 2, F.TYPE_UINT32, opt),
    )
    msg("GenericEvent", ("data", 1, F.TYPE_STRING, opt))
    pool = descriptor_pool.DescriptorPool()
    pool.Add(fdp)
    return {
        m.name: message_factory.GetMessageClass(
            pool.FindMessageTypeByName(f"{ORDER_PACKAGE}.{m.name}")
        )
        for m in fdp.message_type
    }


_ORDER = _build_order_messages()
CreateOrder = _ORDER["CreateOrder"]
GenericCommand = _ORDER["GenericCommand"]
GenericEvent = _ORDER["GenericEvent"]


def order_type_url(name: str) -> str:
    """Type URL of a message ``name`` in the backend's ``order`` package."""
    return f"{TYPE_URL_PREFIX}{ORDER_PACKAGE}.{name}"


def event_name_for(command: str) -> str:
    """Name of the event a command type emits."""
    return {
        "CreateOrder": "OrderCreated",
        "AddItem": "ItemAdded",
        "CancelOrder": "OrderCancelled",
        "ShipOrder": "OrderShipped",
        "ReserveStock": "StockReserved",
    }.get(command, f"{command}Done")


def root_for(label: str) -> uuid.UUID:
    """Root for a scenario label: a UUID literal parses as-is, any other
    label maps through uuid5(NAMESPACE_OID, label)."""
    try:
        return uuid.UUID(label)
    except ValueError:
        return uuid.uuid5(uuid.NAMESPACE_OID, label)


def cover(
    domain: str, root_label: str, edition: str | None = None, correlation: str = ""
) -> Cover:
    c = Cover(domain=domain, correlation_id=correlation)
    c.root.value = root_for(root_label).bytes
    if edition is not None:
        c.edition.CopyFrom(Edition(name=edition))
    return c


def event_any(name: str, data: str) -> ProtoAny:
    return ProtoAny(
        type_url=order_type_url(name), value=GenericEvent(data=data).SerializeToString()
    )


def command_any(name: str, data: str, count: int = 1) -> ProtoAny:
    return ProtoAny(
        type_url=order_type_url(name),
        value=GenericCommand(data=data, count=count).SerializeToString(),
    )


def short_type(any_msg: ProtoAny) -> str:
    """Short type name (``Foo``) of an ``Any``."""
    return any_msg.type_url.rsplit("/", 1)[-1].rsplit(".", 1)[-1]


def event_data(page: EventPage) -> str:
    """``data`` of a backend-emitted event page."""
    return GenericEvent.FromString(page.event.value).data


def page_seq(page: EventPage | CommandPage) -> int | None:
    """Explicit sequence of a page header, if any."""
    if page.header.WhichOneof("sequence_type") == "sequence":
        return page.header.sequence
    return None


def timestamp(rfc3339: str) -> Timestamp:
    ts = Timestamp()
    ts.FromJsonString(rfc3339)
    return ts


def _now() -> Timestamp:
    ts = Timestamp()
    ts.GetCurrentTime()
    return ts


def _ts_key(ts: Timestamp) -> tuple[int, int]:
    return (ts.seconds, ts.nanos)


def _edition_key(c: Cover) -> str:
    name = c.edition.name if c.HasField("edition") else DEFAULT_EDITION
    return "" if name in ("", DEFAULT_EDITION, "angzarr") else name


def _key_of(c: Cover) -> tuple[str, str, bytes]:
    return (_edition_key(c), c.domain, c.root.value)


def _status(code: grpc.StatusCode, details: str) -> StubRpcError:
    return StubRpcError(code, details)


@dataclass
class _Aggregate:
    cover: Cover
    pages: list[EventPage] = field(default_factory=list)
    snapshot: Snapshot | None = None
    correlations: set[str] = field(default_factory=set)

    def next_sequence(self) -> int:
        if self.pages and page_seq(self.pages[-1]) is not None:
            return page_seq(self.pages[-1]) + 1
        if self.snapshot is not None:
            return self.snapshot.sequence + 1
        return 0

    def book(self, pages: list[EventPage] | None = None) -> EventBook:
        b = EventBook(next_sequence=self.next_sequence())
        b.cover.CopyFrom(self.cover)
        b.pages.extend(self.pages if pages is None else pages)
        if self.snapshot is not None:
            b.snapshot.CopyFrom(self.snapshot)
        return b


def _decide(page: CommandPage, history: list[EventPage]) -> list[tuple[str, str]]:
    """Run the scripted aggregate for one command page against ``history``;
    returns the ``(event name, data)`` pairs it emits."""
    if page.WhichOneof("payload") != "command":
        raise _status(grpc.StatusCode.INVALID_ARGUMENT, "missing command payload")
    name = short_type(page.command)
    if not name:
        raise _status(grpc.StatusCode.INVALID_ARGUMENT, "missing command type")
    try:
        if name == "CreateOrder":
            create = CreateOrder.FromString(page.command.value)
        else:
            cmd = GenericCommand.FromString(page.command.value)
    except DecodeError as e:
        raise _status(
            grpc.StatusCode.INVALID_ARGUMENT, f"malformed payload: {e}"
        ) from e
    if name == "CreateOrder":
        if not create.customer_id:
            raise _status(
                grpc.StatusCode.INVALID_ARGUMENT, "missing required field: customer_id"
            )
        return [("OrderCreated", create.customer_id)]
    if name == "CancelOrder" and any(
        short_type(p.event) == "OrderShipped" for p in history
    ):
        raise _status(
            grpc.StatusCode.FAILED_PRECONDITION, "cannot cancel shipped order"
        )
    return [(event_name_for(name), cmd.data)] * max(cmd.count, 1)


def _build_pages(decided: list[tuple[str, str]], first_seq: int) -> list[EventPage]:
    pages = []
    for i, (name, data) in enumerate(decided):
        p = EventPage()
        p.header.sequence = first_seq + i
        p.created_at.CopyFrom(_now())
        p.event.CopyFrom(event_any(name, data))
        pages.append(p)
    return pages


def _command_parts(book: CommandBook | None) -> tuple[Cover, CommandPage]:
    if book is None:
        raise _status(grpc.StatusCode.INVALID_ARGUMENT, "missing command book")
    if not book.HasField("cover"):
        raise _status(grpc.StatusCode.INVALID_ARGUMENT, "missing cover")
    if not book.cover.domain:
        raise _status(grpc.StatusCode.INVALID_ARGUMENT, "domain is required")
    if not book.pages:
        raise _status(grpc.StatusCode.INVALID_ARGUMENT, "missing command page")
    return book.cover, book.pages[0]


def saga_command(source: Cover, page: EventPage, target: str) -> CommandBook:
    """The command a backend saga emits for one source event: a
    ``ReserveStock`` for ``target`` whose header records its provenance."""
    book = CommandBook()
    book.cover.CopyFrom(
        Cover(domain=target, root=source.root, correlation_id=source.correlation_id)
    )
    cp = book.pages.add()
    cp.header.angzarr_deferred.source.CopyFrom(source)
    cp.header.angzarr_deferred.source_seq = page_seq(page) or 0
    cp.command.CopyFrom(command_any("ReserveStock", source.domain, 1))
    return book


class TestBackend:
    """In-process fake of the coordinator gRPC services.

    Serves ``CommandHandlerCoordinatorService``, ``EventQueryService`` and
    the projector / saga / process-manager coordinators' speculative RPCs
    to every :class:`FakeChannel` opened with :meth:`channel`. Requests and
    responses cross the channel serialized, as they would over the wire.
    """

    __test__ = False  # not a pytest test class

    def __init__(self, endpoint: str = "test-backend:1310") -> None:
        self.endpoint = endpoint
        self._lock = threading.RLock()
        self._store: dict[tuple[str, str, bytes], _Aggregate] = {}
        self.known_domains: set[str] | None = None
        self.response_delay: float | None = None
        self.projector_domains: set[str] = set()
        self.sagas: dict[str, str] = {}
        self.projector_runs: list[tuple[str, str, int]] = []
        self.sync_modes: list[int] = []
        self.rpcs: list[tuple[str, int]] = []
        self.pending: list[Callable[[], None]] = []
        self.channels: list[FakeChannel] = []
        self._routes: dict[str, tuple[type, Callable[[Any], Any]]] = {
            "/io.angzarr.v1.CommandHandlerCoordinatorService/HandleCommand": (
                CommandRequest,
                self._handle_command,
            ),
            "/io.angzarr.v1.CommandHandlerCoordinatorService/HandleSyncSpeculative": (
                SpeculateCommandHandlerRequest,
                self._handle_sync_speculative,
            ),
            "/io.angzarr.v1.EventQueryService/GetEventBook": (
                Query,
                self._get_event_book,
            ),
            "/io.angzarr.v1.EventQueryService/GetEvents": (Query, self._select),
            "/io.angzarr.v1.ProjectorCoordinatorService/HandleSpeculative": (
                SpeculateProjectorRequest,
                self._projector_speculative,
            ),
            "/io.angzarr.v1.SagaCoordinatorService/ExecuteSpeculative": (
                SpeculateSagaRequest,
                self._saga_speculative,
            ),
            "/io.angzarr.v1.ProcessManagerCoordinatorService/HandleSpeculative": (
                SpeculatePmRequest,
                self._pm_speculative,
            ),
        }

    # -- connections ------------------------------------------------------

    def channel(self) -> FakeChannel:
        """Open a new connection to this backend."""
        ch = FakeChannel(self, channel_id=len(self.channels))
        self.channels.append(ch)
        return ch

    def open_connections(self) -> int:
        return sum(1 for c in self.channels if not c.closed)

    def rpc_names(self) -> list[str]:
        return [name for name, _ in self.rpcs]

    # -- arrangement ------------------------------------------------------

    def _aggregate(self, c: Cover) -> _Aggregate:
        key = _key_of(c)
        if key not in self._store:
            stored = Cover()
            stored.CopyFrom(c)
            stored.ClearField("correlation_id")
            self._store[key] = _Aggregate(cover=stored)
        return self._store[key]

    def seed(self, c: Cover, name: str, count: int) -> None:
        """Append ``count`` events named ``name`` with data ``"<name>-<seq>"``."""
        with self._lock:
            agg = self._aggregate(c)
            for _ in range(count):
                seq = agg.next_sequence()
                agg.pages.extend(_build_pages([(name, f"{name}-{seq}")], seq))
            if c.correlation_id:
                agg.correlations.add(c.correlation_id)

    def seed_event(
        self, c: Cover, name: str, data: str, at: Timestamp | None = None
    ) -> None:
        """Append one event with explicit payload data and timestamp."""
        with self._lock:
            agg = self._aggregate(c)
            (page,) = _build_pages([(name, data)], agg.next_sequence())
            if at is not None:
                page.created_at.CopyFrom(at)
            agg.pages.append(page)
            if c.correlation_id:
                agg.correlations.add(c.correlation_id)

    def seed_snapshot(self, c: Cover, sequence: int) -> None:
        with self._lock:
            snap = Snapshot(sequence=sequence)
            snap.state.CopyFrom(event_any("OrderState", f"as-of-{sequence}"))
            self._aggregate(c).snapshot = snap

    def stored_pages(self, c: Cover) -> list[EventPage]:
        with self._lock:
            agg = self._store.get(_key_of(c))
            return list(agg.pages) if agg else []

    def total_events(self) -> int:
        with self._lock:
            return sum(len(a.pages) for a in self._store.values())

    def drain(self) -> None:
        """Run the downstream work queued by fire-and-forget commands."""
        while self.pending:
            self.pending.pop(0)()

    # -- dispatch ---------------------------------------------------------

    def invoke(
        self, channel_id: int, path: str, request: Any, timeout: float | None
    ) -> Any:
        if path not in self._routes:
            raise _status(grpc.StatusCode.UNIMPLEMENTED, f"test backend: {path}")
        method = path.rsplit("/", 1)[-1]
        self.rpcs.append((method, channel_id))
        if (
            self.response_delay is not None
            and timeout is not None
            and timeout < self.response_delay
        ):
            raise _status(grpc.StatusCode.DEADLINE_EXCEEDED, "Deadline Exceeded")
        request_cls, handler = self._routes[path]
        return handler(request_cls.FromString(request))

    def _check_domain(self, domain: str) -> None:
        if self.known_domains is not None and domain not in self.known_domains:
            raise _status(grpc.StatusCode.NOT_FOUND, f"unknown domain: {domain}")

    def _apply(self, book: CommandBook | None) -> EventBook:
        """Validate, decide and persist one command; returns the new events."""
        c, page = _command_parts(book)
        self._check_domain(c.domain)
        with self._lock:
            agg = self._aggregate(c)
            nxt = agg.next_sequence()
            seq = page_seq(page)
            if seq is not None and seq != nxt:
                raise _status(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    f"sequence mismatch: expected {nxt}, got {seq}",
                )
            pages = _build_pages(_decide(page, agg.pages), nxt)
            agg.pages.extend(pages)
            if c.correlation_id:
                agg.correlations.add(c.correlation_id)
            out = EventBook(next_sequence=agg.next_sequence())
            out.cover.CopyFrom(c)
            out.pages.extend(pages)
            return out

    def _run_projectors(self, events: EventBook) -> list[Projection]:
        domain = events.cover.domain
        if domain not in self.projector_domains:
            return []
        out = []
        for page in events.pages:
            seq = page_seq(page) or 0
            self.projector_runs.append((f"{domain}-projector", domain, seq))
            p = Projection(projector=f"{domain}-projector", sequence=seq)
            p.cover.CopyFrom(events.cover)
            out.append(p)
        return out

    def _run_sagas(self, events: EventBook) -> None:
        target = self.sagas.get(events.cover.domain)
        if target is None:
            return
        for page in events.pages:
            self._apply(saga_command(events.cover, page, target))

    def _handle_command(self, req: CommandRequest) -> CommandResponse:
        self.sync_modes.append(req.sync_mode)
        events = self._apply(req.command if req.HasField("command") else None)
        resp = CommandResponse()
        resp.events.CopyFrom(events)
        if req.sync_mode == SyncMode.SYNC_MODE_SIMPLE:
            resp.projections.extend(self._run_projectors(events))
        elif req.sync_mode == SyncMode.SYNC_MODE_CASCADE:
            resp.projections.extend(self._run_projectors(events))
            self._run_sagas(events)
        else:

            def downstream(book: EventBook = events) -> None:
                self._run_projectors(book)
                self._run_sagas(book)

            self.pending.append(downstream)
        return resp

    def _handle_sync_speculative(
        self, req: SpeculateCommandHandlerRequest
    ) -> CommandResponse:
        c, page = _command_parts(req.command if req.HasField("command") else None)
        self._check_domain(c.domain)
        with self._lock:
            agg = self._store.get(_key_of(c))
            history = list(agg.pages) if agg else []
        point = (
            req.point_in_time.WhichOneof("point_in_time")
            if req.HasField("point_in_time")
            else None
        )
        if point == "as_of_sequence":
            cap = req.point_in_time.as_of_sequence
            history = [p for p in history if (page_seq(p) or 0) <= cap]
        elif point == "as_of_time":
            cut = _ts_key(req.point_in_time.as_of_time)
            history = [p for p in history if _ts_key(p.created_at) <= cut]
        nxt = page_seq(history[-1]) + 1 if history else 0
        pages = _build_pages(_decide(page, history), nxt)
        resp = CommandResponse()
        resp.events.cover.CopyFrom(c)
        resp.events.pages.extend(pages)
        resp.events.next_sequence = nxt + len(pages)
        return resp

    def _select(self, query: Query) -> list[EventBook]:
        if not query.HasField("cover"):
            raise _status(grpc.StatusCode.INVALID_ARGUMENT, "missing cover")
        c = query.cover
        with self._lock:
            if not c.HasField("root") and c.correlation_id:
                return [
                    a.book()
                    for a in self._store.values()
                    if c.correlation_id in a.correlations
                ]
            if not c.domain:
                raise _status(grpc.StatusCode.INVALID_ARGUMENT, "domain is required")
            agg = self._store.get(_key_of(c)) or _Aggregate(cover=c)
            selection = query.WhichOneof("selection")

            def keep(p: EventPage) -> bool:
                seq = page_seq(p) or 0
                if selection == "range":
                    r = query.range
                    return seq >= r.lower and (
                        not r.HasField("upper") or seq <= r.upper
                    )
                if selection == "sequences":
                    return seq in query.sequences.values
                if selection == "temporal":
                    t = query.temporal
                    which = t.WhichOneof("point_in_time")
                    if which == "as_of_sequence":
                        return seq <= t.as_of_sequence
                    if which == "as_of_time":
                        return _ts_key(p.created_at) <= _ts_key(t.as_of_time)
                return True

            book = agg.book([p for p in agg.pages if keep(p)])
            book.cover.CopyFrom(c)
            return [book]

    def _get_event_book(self, query: Query) -> EventBook:
        books = self._select(query)
        return books[0] if books else EventBook()

    def _projector_speculative(self, req: SpeculateProjectorRequest) -> Projection:
        """The ``order-summary`` projector: projects the sequences it saw,
        in order, without recording a projector run."""
        if not req.HasField("events"):
            raise _status(grpc.StatusCode.INVALID_ARGUMENT, "missing events")
        seqs = [page_seq(p) for p in req.events.pages if page_seq(p) is not None]
        p = Projection(projector="order-summary", sequence=seqs[-1] if seqs else 0)
        p.cover.CopyFrom(req.events.cover)
        p.projection.CopyFrom(event_any("OrderSummary", ",".join(map(str, seqs))))
        return p

    def _saga_speculative(self, req: SpeculateSagaRequest) -> SagaResponse:
        """Sagas translate every source event into a ``ReserveStock`` for
        ``inventory`` (from ``orders``) or ``orders`` (from anything else),
        without delivering it."""
        if not (req.HasField("request") and req.request.HasField("source")):
            raise _status(grpc.StatusCode.INVALID_ARGUMENT, "missing saga source")
        source = req.request.source
        if not source.HasField("cover"):
            raise _status(grpc.StatusCode.INVALID_ARGUMENT, "missing source cover")
        target = "inventory" if source.cover.domain == "orders" else "orders"
        resp = SagaResponse()
        resp.commands.extend(
            saga_command(source.cover, p, target) for p in source.pages
        )
        return resp

    def _pm_speculative(self, req: SpeculatePmRequest) -> ProcessManagerHandleResponse:
        """The ``order-workflow`` PM: requires a correlation id and emits one
        ``shipping`` command per trigger event."""
        if not (req.HasField("request") and req.request.HasField("trigger")):
            raise _status(grpc.StatusCode.INVALID_ARGUMENT, "missing trigger")
        trigger = req.request.trigger
        if not trigger.cover.correlation_id:
            raise _status(
                grpc.StatusCode.INVALID_ARGUMENT, "correlation_id is required"
            )
        resp = ProcessManagerHandleResponse()
        resp.commands.extend(
            saga_command(trigger.cover, p, "shipping") for p in trigger.pages
        )
        return resp


class FakeChannel:
    """A ``grpc.Channel`` look-alike that delivers RPCs to a
    :class:`TestBackend` in-process.

    The generated service stubs are built on it unchanged. A channel with
    no backend behaves like one whose endpoint has nothing listening:
    every RPC fails ``UNAVAILABLE``. After :meth:`close`, invoking an RPC
    raises ``ValueError`` exactly as ``grpc.Channel`` does.
    """

    def __init__(self, backend: TestBackend | None, channel_id: int = -1) -> None:
        self._backend = backend
        self.channel_id = channel_id
        self.closed = False

    def _call(
        self, path: str, serializer: Callable, request: Any, timeout: float | None
    ) -> Any:
        if self.closed:
            raise ValueError("Cannot invoke RPC on closed channel!")
        if self._backend is None:
            raise _status(
                grpc.StatusCode.UNAVAILABLE, "failed to connect to all addresses"
            )
        return self._backend.invoke(self.channel_id, path, serializer(request), timeout)

    def unary_unary(
        self, path, request_serializer=None, response_deserializer=None, **_
    ):
        def call(request, timeout=None, metadata=None, **__):
            resp = self._call(path, request_serializer, request, timeout)
            return response_deserializer(resp.SerializeToString())

        return call

    def unary_stream(
        self, path, request_serializer=None, response_deserializer=None, **_
    ):
        def call(request, timeout=None, metadata=None, **__) -> Iterator[Any]:
            resps = self._call(path, request_serializer, request, timeout)
            return iter([response_deserializer(r.SerializeToString()) for r in resps])

        return call

    def stream_unary(self, path, **_):
        def call(*_a, **_k):
            raise _status(grpc.StatusCode.UNIMPLEMENTED, f"test backend: {path}")

        return call

    stream_stream = stream_unary

    def subscribe(self, callback, try_to_connect=False) -> None:
        pass

    def unsubscribe(self, callback) -> None:
        pass

    def close(self) -> None:
        self.closed = True


def route_endpoints(monkeypatch, *backends: TestBackend) -> None:
    """Make the client's endpoint dialing reach the given backends.

    ``connect`` / ``from_env`` / ``for_domain`` resolve an endpoint string
    and hand it to ``angzarr_client.client._create_channel``; this routes
    each backend's ``endpoint`` to a new :class:`FakeChannel` on it and any
    other endpoint to a channel with nothing listening.
    """
    by_endpoint = {b.endpoint: b for b in backends}

    def create_channel(endpoint: str) -> FakeChannel:
        backend = by_endpoint.get(endpoint)
        return backend.channel() if backend is not None else FakeChannel(None)

    monkeypatch.setattr(client_module, "_create_channel", create_channel)
