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
    `CommandHandlerClient`, `QueryClient`.

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
from google.protobuf.empty_pb2 import Empty
from google.protobuf.message import DecodeError
from google.protobuf.timestamp_pb2 import Timestamp

from angzarr_client import client as client_module
from angzarr_client._pb import (
    CommandBook,
    CommandPage,
    CommandResponse,
    Cover,
    EventBook,
    EventMeta,
    EventPage,
    Projection,
    CorrelationQuery,
    Query,
    Snapshot,
)
from angzarr_client.helpers import TYPE_URL_PREFIX


class StubRpcError(grpc.RpcError):
    """Minimal `grpc.RpcError` carrying a code() and details().

    The production code paths (`QueryClient.get_event_book`,
    `CommandHandlerClient.handle`, etc.) catch
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
        stub.errors["Handle"] = StubRpcError(
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
# - a command whose Handling sets expected_sequence must name the aggregate's
#   next sequence (STRICT optimistic concurrency, ABORTED otherwise);
# - the command page's correlation_id is copied onto every event it records
#   (EventMeta.correlation_id).
#
# Command modes (command_handler.proto, command_modes): ``Handle`` (DECISION)
# returns the persisted events and queues the projectors (run them with
# :meth:`TestBackend.drain`), ``HandleAsync`` (ASYNC) queues the whole command
# and returns Empty, ``HandleSync`` (SIMPLE) also runs the configured
# projectors before replying, in ``outcome.projections``.

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


# Correlation IDs are UUIDs on the wire (CommandPage.correlation_id,
# EventMeta.correlation_id); scenario labels map to one as roots do.
correlation_for = root_for


def cover(domain: str, root_label: str, edition: str | None = None) -> Cover:
    c = Cover(domain=domain)
    c.root.value = root_for(root_label).bytes
    if edition is not None:
        c.edition = edition
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
    return GenericEvent.FromString(page.payload.value).data


def page_seq(page: EventPage) -> int:
    """The page's sequence in its stream."""
    return page.sequence


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


def _key_of(c: Cover) -> tuple[str, str, bytes]:
    return (c.edition if c.HasField("edition") else "", c.domain, c.root.value)


def _status(code: grpc.StatusCode, details: str) -> StubRpcError:
    return StubRpcError(code, details)


@dataclass
class _Aggregate:
    cover: Cover
    pages: list[EventPage] = field(default_factory=list)
    snapshot: Snapshot | None = None

    def next_sequence(self) -> int:
        if self.pages:
            return self.pages[-1].sequence + 1
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
    if page.WhichOneof("body") != "command":
        raise _status(grpc.StatusCode.INVALID_ARGUMENT, "missing command payload")
    payload = page.command.payload
    name = short_type(payload)
    if not name:
        raise _status(grpc.StatusCode.INVALID_ARGUMENT, "missing command type")
    try:
        if name == "CreateOrder":
            create = CreateOrder.FromString(payload.value)
        else:
            cmd = GenericCommand.FromString(payload.value)
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
        short_type(p.payload) == "OrderShipped" for p in history
    ):
        raise _status(
            grpc.StatusCode.FAILED_PRECONDITION, "cannot cancel shipped order"
        )
    return [(event_name_for(name), cmd.data)] * max(cmd.count, 1)


def _build_pages(
    decided: list[tuple[str, str]],
    first_seq: int,
    correlation_id: uuid.UUID | None = None,
) -> list[EventPage]:
    pages = []
    for i, (name, data) in enumerate(decided):
        meta = EventMeta(recorded_at=_now())
        if correlation_id is not None:
            meta.correlation_id.value = correlation_id.bytes
        pages.append(
            EventPage(payload=event_any(name, data), sequence=first_seq + i, meta=meta)
        )
    return pages


def _command_parts(book: CommandBook) -> tuple[Cover, CommandPage]:
    if not book.HasField("cover"):
        raise _status(grpc.StatusCode.INVALID_ARGUMENT, "missing cover")
    if not book.cover.domain:
        raise _status(grpc.StatusCode.INVALID_ARGUMENT, "domain is required")
    if not book.HasField("page"):
        raise _status(grpc.StatusCode.INVALID_ARGUMENT, "missing command page")
    return book.cover, book.page


class TestBackend:
    """In-process fake of the coordinator gRPC services.

    Serves ``CommandHandlerCoordinatorService`` (Handle, HandleAsync,
    HandleSync) and ``EventQueryService`` to every :class:`FakeChannel`
    opened with :meth:`channel`. Requests and responses cross the channel
    serialized, as they would over the wire.
    """

    __test__ = False  # not a pytest test class

    def __init__(self, endpoint: str = "test-backend:1310") -> None:
        self.endpoint = endpoint
        self._lock = threading.RLock()
        self._store: dict[tuple[str, str, bytes], _Aggregate] = {}
        self.known_domains: set[str] | None = None
        self.response_delay: float | None = None
        self.projector_domains: set[str] = set()
        self.projector_runs: list[tuple[str, str, int]] = []
        self.rpcs: list[tuple[str, int]] = []
        self.pending: list[Callable[[], None]] = []
        self.channels: list[FakeChannel] = []
        self._routes: dict[str, tuple[type, Callable[[Any], Any]]] = {
            "/io.angzarr.v1.CommandHandlerCoordinatorService/Handle": (
                CommandBook,
                self._handle,
            ),
            "/io.angzarr.v1.CommandHandlerCoordinatorService/HandleAsync": (
                CommandBook,
                self._handle_async,
            ),
            "/io.angzarr.v1.CommandHandlerCoordinatorService/HandleSync": (
                CommandBook,
                self._handle_sync,
            ),
            "/io.angzarr.v1.EventQueryService/GetEventBook": (
                Query,
                self._get_event_book,
            ),
            "/io.angzarr.v1.EventQueryService/GetEvents": (Query, self._select),
            "/io.angzarr.v1.EventQueryService/GetCorrelatedEvents": (
                CorrelationQuery,
                self._correlated,
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
            self._store[key] = _Aggregate(cover=stored)
        return self._store[key]

    def seed(
        self, c: Cover, name: str, count: int, correlation: uuid.UUID | None = None
    ) -> None:
        """Append ``count`` events named ``name`` with data ``"<name>-<seq>"``."""
        with self._lock:
            agg = self._aggregate(c)
            for _ in range(count):
                seq = agg.next_sequence()
                agg.pages.extend(
                    _build_pages([(name, f"{name}-{seq}")], seq, correlation)
                )

    def seed_event(
        self, c: Cover, name: str, data: str, at: Timestamp | None = None
    ) -> None:
        """Append one event with explicit payload data and timestamp."""
        with self._lock:
            agg = self._aggregate(c)
            (page,) = _build_pages([(name, data)], agg.next_sequence())
            if at is not None:
                page.meta.recorded_at.CopyFrom(at)
            agg.pages.append(page)

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
        """Run the work queued by DECISION and ASYNC commands."""
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

    def _apply(self, book: CommandBook) -> EventBook:
        """Validate, decide and persist one command; returns the new events."""
        c, page = _command_parts(book)
        self._check_domain(c.domain)
        with self._lock:
            agg = self._aggregate(c)
            nxt = agg.next_sequence()
            handling = book.handling
            if handling.HasField("expected_sequence") and (
                handling.expected_sequence != nxt
            ):
                raise _status(
                    grpc.StatusCode.ABORTED,
                    f"sequence mismatch: expected {handling.expected_sequence}, "
                    f"stream at {nxt}",
                )
            correlation = (
                uuid.UUID(bytes=page.correlation_id.value)
                if page.HasField("correlation_id")
                else None
            )
            pages = _build_pages(_decide(page, agg.pages), nxt, correlation)
            agg.pages.extend(pages)
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
            self.projector_runs.append((f"{domain}-projector", domain, page.sequence))
            p = Projection(
                projector=f"{domain}-projector", next_sequence=page.sequence + 1
            )
            p.cover.CopyFrom(events.cover)
            out.append(p)
        return out

    def _handle(self, book: CommandBook) -> CommandResponse:
        events = self._apply(book)
        self.pending.append(lambda: self._run_projectors(events))
        return CommandResponse(events=events)

    def _handle_async(self, book: CommandBook) -> Empty:
        _command_parts(book)
        self._check_domain(book.cover.domain)
        self.pending.append(lambda: self._run_projectors(self._apply(book)))
        return Empty()

    def _handle_sync(self, book: CommandBook) -> CommandResponse:
        events = self._apply(book)
        resp = CommandResponse(events=events)
        resp.outcome.projections.extend(self._run_projectors(events))
        return resp

    def _select(self, query: Query) -> list[EventBook]:
        if not query.HasField("cover"):
            raise _status(grpc.StatusCode.INVALID_ARGUMENT, "missing cover")
        c = query.cover
        if not c.domain:
            raise _status(grpc.StatusCode.INVALID_ARGUMENT, "domain is required")
        correlation = (
            query.correlation_id.value if query.HasField("correlation_id") else None
        )
        selection = query.WhichOneof("selection")

        def keep(p: EventPage) -> bool:
            if correlation is not None and p.meta.correlation_id.value != correlation:
                return False
            seq = p.sequence
            if selection == "range":
                r = query.range
                return seq >= r.lower and (not r.HasField("upper") or seq <= r.upper)
            if selection == "sequences":
                return seq in query.sequences.values
            if selection == "temporal":
                t = query.temporal
                which = t.WhichOneof("point_in_time")
                if which == "as_of_sequence":
                    return seq <= t.as_of_sequence
                if which == "as_of_time":
                    return _ts_key(p.meta.recorded_at) <= _ts_key(t.as_of_time)
            return True

        with self._lock:
            if not c.HasField("root"):
                # By correlation: every stream of the domain is searched,
                # one EventBook per matching stream.
                books = []
                for agg in self._store.values():
                    if agg.cover.domain != c.domain:
                        continue
                    pages = [p for p in agg.pages if keep(p)]
                    if pages:
                        books.append(agg.book(pages))
                return books
            agg = self._store.get(_key_of(c)) or _Aggregate(cover=c)
            book = agg.book([p for p in agg.pages if keep(p)])
            book.cover.CopyFrom(c)
            return [book]

    def _correlated(self, query: CorrelationQuery) -> list[EventBook]:
        """Every stream's pages carrying the correlation, across all
        domains: one EventBook per stream (query.proto,
        GetCorrelatedEvents)."""
        if len(query.correlation_id.value) != 16:
            raise _status(grpc.StatusCode.INVALID_ARGUMENT, "correlation_id")
        flow = query.correlation_id.value
        with self._lock:
            books = []
            for agg in self._store.values():
                pages = [p for p in agg.pages if p.meta.correlation_id.value == flow]
                if pages:
                    books.append(agg.book(pages))
            return books

    def _get_event_book(self, query: Query) -> EventBook:
        books = self._select(query)
        return books[0] if books else EventBook()


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
