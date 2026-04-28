"""R9: sequence numbers increment from a single handler's emissions.

Audit finding #18 (formerly #51): multi-handler CommandHandler dispatch
is forbidden — at most one CH per (domain, command_type) within a
Router. This file used to pin the cross-handler sequence-merge contract
(handler A emits N events at seq=k, handler B sees seq=k+N, etc.). With
single-handler CH, that contract is moot; only the single-handler case
below remains.
"""

from __future__ import annotations

from dataclasses import dataclass

from google.protobuf.any_pb2 import Any as ProtoAny

from angzarr_client.helpers import TYPE_URL_PREFIX
from angzarr_client.proto.angzarr import (
    CommandBook,
    CommandPage,
    ContextualCommand,
    Cover,
    EventBook,
    PageHeader,
)
from angzarr_client.router import (
    Router,
    command_handler,
    handles,
)
from tests.fixtures import (
    CreateOrder,
)


@dataclass
class S:
    pass


def _request_with_next_seq(
    next_seq: int, cmd, domain: str = "order"
) -> ContextualCommand:
    book = EventBook()
    book.cover.CopyFrom(Cover(domain=domain))
    book.next_sequence = next_seq

    any_cmd = ProtoAny()
    any_cmd.type_url = TYPE_URL_PREFIX + cmd.DESCRIPTOR.full_name
    any_cmd.value = cmd.SerializeToString()

    cpage = CommandPage()
    cpage.header.CopyFrom(PageHeader(sequence=next_seq))
    cpage.command.CopyFrom(any_cmd)

    cbook = CommandBook()
    cbook.cover.CopyFrom(Cover(domain=domain))
    cbook.pages.append(cpage)

    req = ContextualCommand()
    req.command.CopyFrom(cbook)
    req.events.CopyFrom(book)
    return req


# --------------------------------------------------------------------------
# Seq passed to each handler
# --------------------------------------------------------------------------


# Audit #18: multi-handler CH dispatch is forbidden, so the four
# cross-handler-sequence-threading tests previously here are removed.
# Their contract is moot under single-handler-per-(domain, type).


def test_single_handler_seq_matches_next_sequence():
    seen = []

    @command_handler(domain="order", state=S)
    class Only:
        @handles(CreateOrder)
        def on(self, cmd, state, seq):
            seen.append(seq)
            return None

    router = Router("agg").with_handler(Only, lambda: Only()).build()
    router.dispatch(_request_with_next_seq(99, CreateOrder(order_id="x")))
    assert seen == [99]
