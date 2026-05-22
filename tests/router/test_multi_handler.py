"""R8: dispatch with multiple registered CommandHandlers.

Audit finding #18 (formerly #51): multi-handler CommandHandler dispatch
is forbidden — at most one CH per (domain, command_type) within a
Router. The previous output-merge / sequence-threading / state-isolation
contracts are moot under that rule.

What's still tested here:
  - Build-time rejection of duplicate (domain, command_type) (the new
    DuplicateCommandHandler BuildError).
  - A single CH emitting multiple pages (tuple return).
  - Dispatch routing only the matching handler when multiple CHs cover
    different command types in the same domain.
"""

from __future__ import annotations

from dataclasses import dataclass

from google.protobuf.any_pb2 import Any as ProtoAny

from angzarr_client.helpers import TYPE_URL_PREFIX
from angzarr_client.proto.angzarr.v1.types_pb2 import (
    CommandBook,
    CommandPage,
    ContextualCommand,
    Cover,
    EventBook,
    EventPage,
    PageHeader,
)
from angzarr_client.router import (
    Router,
    command_handler,
    handles,
)
from tests.fixtures import (
    CreateOrder,
    OrderCompleted,
    OrderCreated,
)


@dataclass
class StateA:
    count: int = 0


@dataclass
class StateB:
    count: int = 0


def _request(
    cmd, prior: list | None = None, domain: str = "order"
) -> ContextualCommand:
    prior = prior or []
    book = EventBook()
    book.cover.CopyFrom(Cover(domain=domain))
    for offset, evt in enumerate(prior):
        page = EventPage()
        page.header.CopyFrom(PageHeader(sequence=offset))
        any_msg = ProtoAny()
        any_msg.type_url = TYPE_URL_PREFIX + evt.DESCRIPTOR.full_name
        any_msg.value = evt.SerializeToString()
        page.event.CopyFrom(any_msg)
        book.pages.append(page)
    book.next_sequence = len(prior)

    any_cmd = ProtoAny()
    any_cmd.type_url = TYPE_URL_PREFIX + cmd.DESCRIPTOR.full_name
    any_cmd.value = cmd.SerializeToString()
    cpage = CommandPage()
    cpage.header.CopyFrom(PageHeader(sequence=len(prior)))
    cpage.command.CopyFrom(any_cmd)
    cbook = CommandBook()
    cbook.cover.CopyFrom(Cover(domain=domain))
    cbook.pages.append(cpage)

    req = ContextualCommand()
    req.command.CopyFrom(cbook)
    req.events.CopyFrom(book)
    return req


# --------------------------------------------------------------------------
# Audit #18: builder rejects duplicate (domain, type_url)
# --------------------------------------------------------------------------


def test_builder_rejects_duplicate_command_handler_for_same_domain_and_type():
    """Audit finding #18: two CommandHandlers covering the same
    (domain, command_type) within one Router are rejected at build
    time. The merged-output / state-isolation tests previously here
    are removed — those contracts no longer apply.
    """
    import pytest

    from angzarr_client.router import BuildError

    @command_handler(domain="order", state=StateA)
    class First:
        @handles(CreateOrder)
        def on(self, cmd, state, seq):
            return None

    @command_handler(domain="order", state=StateB)
    class Second:
        @handles(CreateOrder)
        def on(self, cmd, state, seq):
            return None

    with pytest.raises(BuildError, match="duplicate"):
        (
            Router("agg")
            .with_handler(First, lambda: First())
            .with_handler(Second, lambda: Second())
            .build()
        )


def test_handler_emitting_tuple_yields_multiple_pages():
    @command_handler(domain="order", state=StateA)
    class Multi:
        @handles(CreateOrder)
        def on(self, cmd, state, seq):
            return (
                OrderCreated(order_id=cmd.order_id),
                OrderCompleted(order_id=cmd.order_id),
            )

    router = Router("agg").with_handler(Multi, lambda: Multi()).build()
    response = router.dispatch(_request(CreateOrder(order_id="o-1")))

    pages = response.events.pages
    assert len(pages) == 2
    assert pages[0].event.type_url.endswith("OrderCreated")
    assert pages[1].event.type_url.endswith("OrderCompleted")


def test_only_matching_handlers_invoked():
    call_order = []

    @command_handler(domain="order", state=StateA)
    class HandlesCreate:
        @handles(CreateOrder)
        def on(self, cmd, state, seq):
            call_order.append("create")
            return None

    @command_handler(domain="order", state=StateB)
    class HandlesComplete:
        @handles(OrderCompleted)
        def on(self, cmd, state, seq):
            call_order.append("complete")
            return None

    router = (
        Router("agg")
        .with_handler(HandlesCreate, lambda: HandlesCreate())
        .with_handler(HandlesComplete, lambda: HandlesComplete())
        .build()
    )
    router.dispatch(_request(CreateOrder(order_id="o-1")))

    assert call_order == ["create"]
