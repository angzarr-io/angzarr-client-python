"""R14: gRPC servicer adapters for the new runtime routers.

The unified runtime routers (``CommandHandlerRouter``, ``SagaRouter``,
``ProcessManagerRouter``, ``ProjectorRouter``) have ``.dispatch(request)``
methods that match the shape of the gRPC servicer methods. This round
provides thin adapter servicers in ``router_v2/server.py`` that plug a
unified router into the corresponding generated gRPC service classes.

Tests exercise a full roundtrip through the adapter: synthetic
ContextualCommand in → BusinessResponse out, with CommandRejectedError
translated to FAILED_PRECONDITION.

Existing ``tests/features/`` Gherkin scenarios are unaffected — they use
the old handler path which remains intact until R15.
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import Mock

import grpc
from google.protobuf.any_pb2 import Any as ProtoAny

from angzarr_client.errors import CommandRejectedError
from angzarr_client.helpers import TYPE_URL_PREFIX
from angzarr_client.proto.angzarr import (
    CommandBook,
    CommandPage,
    ContextualCommand,
    Cover,
    EventBook,
    PageHeader,
    SagaHandleRequest,
)
from angzarr_client.proto.angzarr import process_manager_pb2 as pm_pb
from angzarr_client.router import (
    Router,
    command_handler,
    handles,
    projector,
    process_manager,
    saga,
)
from angzarr_client.router.server import (
    CommandHandlerGrpc,
    ProcessManagerGrpc,
    ProjectorGrpc,
    SagaGrpc,
)
from tests.fixtures import (
    CreateOrder,
    OrderCompleted,
    OrderCreated,
)


@dataclass
class State:
    created: bool = False


def _contextual_command(cmd, domain: str = "order") -> ContextualCommand:
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
    return req


# --------------------------------------------------------------------------
# CommandHandlerGrpc adapter
# --------------------------------------------------------------------------


def test_command_handler_grpc_handles_successful_command():
    @command_handler(domain="order", state=State)
    class Agg:
        @handles(CreateOrder)
        def on(self, cmd, state, seq):
            return OrderCreated(order_id=cmd.order_id)

    router = Router("agg").with_handler(Agg, lambda: Agg()).build()
    servicer = CommandHandlerGrpc(router)

    ctx = Mock(spec=grpc.ServicerContext)
    response = servicer.Handle(_contextual_command(CreateOrder(order_id="o-1")), ctx)

    assert response.HasField("events")
    assert len(response.events.pages) == 1


def test_command_handler_grpc_translates_rejected_error_to_failed_precondition():
    @command_handler(domain="order", state=State)
    class Agg:
        @handles(CreateOrder)
        def on(self, cmd, state, seq):
            raise CommandRejectedError("business rule violated")

    router = Router("agg").with_handler(Agg, lambda: Agg()).build()
    servicer = CommandHandlerGrpc(router)

    ctx = Mock(spec=grpc.ServicerContext)
    servicer.Handle(_contextual_command(CreateOrder(order_id="o-1")), ctx)

    # The servicer should have called context.abort with FAILED_PRECONDITION.
    ctx.abort.assert_called_once()
    code, _msg = ctx.abort.call_args[0]
    assert code == grpc.StatusCode.FAILED_PRECONDITION


def test_command_handler_grpc_translates_dispatch_error_to_invalid_argument():
    @command_handler(domain="order", state=State)
    class Agg:
        @handles(CreateOrder)
        def on(self, cmd, state, seq):
            return None

    router = Router("agg").with_handler(Agg, lambda: Agg()).build()
    servicer = CommandHandlerGrpc(router)
    ctx = Mock(spec=grpc.ServicerContext)

    # Unknown type_url triggers DispatchError(INVALID_ARGUMENT)
    servicer.Handle(_contextual_command(OrderCompleted(order_id="o-1")), ctx)
    ctx.abort.assert_called_once()
    code, _msg = ctx.abort.call_args[0]
    assert code == grpc.StatusCode.INVALID_ARGUMENT


def _abort_code_for(exc_to_raise: Exception) -> grpc.StatusCode:
    """Drive a CommandHandlerGrpc dispatch with `exc_to_raise` and return the
    gRPC status code the adapter chose."""

    @command_handler(domain="order", state=State)
    class Agg:
        @handles(CreateOrder)
        def on(self, cmd, state, seq):
            raise exc_to_raise

    router = Router("agg").with_handler(Agg, lambda: Agg()).build()
    servicer = CommandHandlerGrpc(router)
    ctx = Mock(spec=grpc.ServicerContext)
    servicer.Handle(_contextual_command(CreateOrder(order_id="o-1")), ctx)
    ctx.abort.assert_called_once()
    code, _ = ctx.abort.call_args[0]
    return code


def test_command_rejected_error_invalid_argument_status_maps_to_invalid_argument():
    # Previously collapsed every CommandRejectedError to FAILED_PRECONDITION,
    # losing the rejection's own status_code. Mirrors Rust's From<CommandRejectedError>.
    # Audit #59: factories now take (code, message, details).
    err = CommandRejectedError.invalid_argument("BAD_INPUT", "bad input")
    assert _abort_code_for(err) == grpc.StatusCode.INVALID_ARGUMENT


def test_command_rejected_error_not_found_status_maps_to_not_found():
    err = CommandRejectedError.not_found("MISSING_AGGREGATE", "missing aggregate")
    assert _abort_code_for(err) == grpc.StatusCode.NOT_FOUND


def test_invalid_argument_error_maps_to_invalid_argument():
    from angzarr_client.errors import InvalidArgumentError

    assert (
        _abort_code_for(InvalidArgumentError("bad")) == grpc.StatusCode.INVALID_ARGUMENT
    )


def test_invalid_timestamp_error_maps_to_invalid_argument():
    from angzarr_client.errors import InvalidTimestampError

    assert (
        _abort_code_for(InvalidTimestampError("not rfc3339"))
        == grpc.StatusCode.INVALID_ARGUMENT
    )


def test_connection_error_maps_to_unavailable():
    from angzarr_client.errors import ConnectionError as AngzarrConnectionError

    assert _abort_code_for(AngzarrConnectionError("dns")) == grpc.StatusCode.UNAVAILABLE


def test_transport_error_maps_to_unavailable():
    from angzarr_client.errors import TransportError

    assert (
        _abort_code_for(TransportError(RuntimeError("eof")))
        == grpc.StatusCode.UNAVAILABLE
    )


def test_grpc_error_passes_through_upstream_code():
    import grpc
    from angzarr_client.errors import GRPCError

    class _RpcErr(grpc.RpcError):
        def code(self):
            return grpc.StatusCode.RESOURCE_EXHAUSTED

        def details(self):
            return "rate limited"

    # GRPCError inspects the upstream code via .grpc_code (post-#59 rename).
    err = GRPCError(_RpcErr())
    abort_code = _abort_code_for(err)
    assert abort_code == grpc.StatusCode.RESOURCE_EXHAUSTED


# --------------------------------------------------------------------------
# SagaGrpc adapter
# --------------------------------------------------------------------------


def test_saga_grpc_handles_event_translation():
    @saga(name="s", source="order", target="inventory")
    class S:
        @handles(OrderCreated)
        def on(self, event, destinations):
            cb = CommandBook()
            cb.cover.CopyFrom(Cover(domain="inventory"))
            return cb

    router = Router("sagas").with_handler(S, lambda: S()).build()
    servicer = SagaGrpc(router)

    # Build SagaHandleRequest
    book = EventBook()
    book.cover.CopyFrom(Cover(domain="order"))
    any_evt = ProtoAny()
    any_evt.type_url = TYPE_URL_PREFIX + OrderCreated.DESCRIPTOR.full_name
    any_evt.value = OrderCreated(order_id="o-1").SerializeToString()
    from angzarr_client.proto.angzarr import EventPage

    page = EventPage()
    page.header.CopyFrom(PageHeader(sequence=0))
    page.event.CopyFrom(any_evt)
    book.pages.append(page)

    req = SagaHandleRequest()
    req.source.CopyFrom(book)

    ctx = Mock(spec=grpc.ServicerContext)
    response = servicer.Handle(req, ctx)

    assert len(response.commands) == 1


# --------------------------------------------------------------------------
# ProcessManagerGrpc adapter
# --------------------------------------------------------------------------


def test_pm_grpc_handles_trigger_event():
    from angzarr_client.router import ProcessManagerResponse

    @process_manager(
        name="pm",
        pm_domain="fulfillment",
        sources=["order"],
        targets=["inventory"],
        state=State,
    )
    class PM:
        @handles(OrderCreated)
        def on(self, event, state, destinations):
            cb = CommandBook()
            cb.cover.CopyFrom(Cover(domain="inventory"))
            return ProcessManagerResponse(commands=[cb])

    router = Router("pms").with_handler(PM, lambda: PM()).build()
    servicer = ProcessManagerGrpc(router)

    # Build PM handle request
    req = pm_pb.ProcessManagerHandleRequest()
    book = EventBook()
    book.cover.CopyFrom(Cover(domain="order"))
    any_evt = ProtoAny()
    any_evt.type_url = TYPE_URL_PREFIX + OrderCreated.DESCRIPTOR.full_name
    any_evt.value = OrderCreated(order_id="o-1").SerializeToString()
    from angzarr_client.proto.angzarr import EventPage

    page = EventPage()
    page.header.CopyFrom(PageHeader(sequence=0))
    page.event.CopyFrom(any_evt)
    book.pages.append(page)
    req.trigger.CopyFrom(book)

    ctx = Mock(spec=grpc.ServicerContext)
    response = servicer.Handle(req, ctx)

    assert len(response.commands) == 1


# --------------------------------------------------------------------------
# ProjectorGrpc adapter
# --------------------------------------------------------------------------


def test_projector_grpc_handles_event_book():
    written = []

    @projector(name="prj", domains=["order"])
    class Output:
        @handles(OrderCreated)
        def on(self, event):
            written.append(event.order_id)

    router = Router("prjs").with_handler(Output, lambda: Output()).build()
    servicer = ProjectorGrpc(router)

    book = EventBook()
    book.cover.CopyFrom(Cover(domain="order"))
    any_evt = ProtoAny()
    any_evt.type_url = TYPE_URL_PREFIX + OrderCreated.DESCRIPTOR.full_name
    any_evt.value = OrderCreated(order_id="o-1").SerializeToString()
    from angzarr_client.proto.angzarr import EventPage

    page = EventPage()
    page.header.CopyFrom(PageHeader(sequence=0))
    page.event.CopyFrom(any_evt)
    book.pages.append(page)

    ctx = Mock(spec=grpc.ServicerContext)
    response = servicer.Handle(book, ctx)

    assert written == ["o-1"]
    assert response.cover.domain == "order"
