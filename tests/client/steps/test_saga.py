"""Step defs for features/saga.feature."""

from __future__ import annotations

from pytest_bdd import given, parsers, scenarios, then, when

from angzarr_client.proto.angzarr import EventBook
from angzarr_client.router import Router, handles, saga
from tests.client.steps._helpers import command_book, saga_request
from tests.fixtures import CreateShipment, OrderCreated, ReserveStock, StockReserved

scenarios("saga.feature")


@given(
    parsers.parse('a saga "OrderFulfillment" translating from "order" to "inventory"')
)
def _given_saga_class(world):
    @saga(name="saga-order-fulfillment", source="order", target="inventory")
    class OrderFulfillment:
        pass

    world.classes["OrderFulfillment"] = OrderFulfillment


@given("the saga handles OrderCreated by emitting a ReserveStock command")
def _given_saga_handler(world):
    cls = world.classes["OrderFulfillment"]
    observed = world.observed_dest

    @handles(OrderCreated)
    def translate(self, event, destinations):
        observed["inventory"] = destinations.sequence_for("inventory")
        observed["fulfillment"] = destinations.sequence_for("fulfillment")
        return command_book(
            ReserveStock(order_id=event.order_id, sku="sku-1", quantity=1),
            target_domain="inventory",
        )

    cls.translate = translate


@given("the router is built with the OrderFulfillment saga")
def _given_saga_built(world):
    world.handlers.append(world.classes["OrderFulfillment"]())
    h = world.handlers[0]
    world.router = Router("sagas").with_handler(type(h), lambda h=h: h).build()


@when("an OrderCreated event is dispatched to the saga router")
def _when_saga_dispatch_ordercreated(world):
    req = saga_request(
        [OrderCreated(order_id="o-1", customer_id="c-1")],
        source_domain="order",
        dest_seqs=world.dest_seqs or None,
    )
    # Audit #86: thread the configured source edition into the request's
    # source cover so dispatch can propagate it to outgoing books.
    if world.source_edition is not None:
        name, divergences = world.source_edition
        req.source.cover.edition.name = name
        for dom, seq in divergences:
            req.source.cover.edition.divergences.add(domain=dom, sequence=seq)
    world.response = world.router.dispatch(req)


@when("a StockReserved event is dispatched to the saga router")
def _when_saga_dispatch_stockreserved(world):
    world.response = world.router.dispatch(
        saga_request(
            [StockReserved(order_id="o-1", sku="sku-1", quantity=1)],
            source_domain="order",
        )
    )


@then("the response contains exactly one command")
def _then_one_command(world):
    assert len(world.response.commands) == 1


@then(parsers.parse('the command targets the "{domain}" domain'))
def _then_command_targets(world, domain):
    assert world.response.commands[0].cover.domain == domain


@then("the response contains no commands")
def _then_no_commands(world):
    assert len(world.response.commands) == 0


@given(parsers.parse("destination sequences inventory={inv:d} and fulfillment={ful:d}"))
def _given_dest_seqs(world, inv, ful):
    world.dest_seqs = {"inventory": inv, "fulfillment": ful}


@then(parsers.parse("the saga observed destination inventory = {n:d}"))
def _then_saga_inv(world, n):
    assert world.observed_dest["inventory"] == n


@then(parsers.parse("the saga observed destination fulfillment = {n:d}"))
def _then_saga_ful(world, n):
    assert world.observed_dest["fulfillment"] == n


# --------------------------------------------------------------------------
# Multi-destination sequence stamping (C-0053)
# --------------------------------------------------------------------------


@given(
    parsers.parse('a saga "OrderSplit" translating from "{source}" to "{a}" and "{b}"')
)
def _given_saga_multi_target(world, source, a, b):
    @saga(name="saga-order-split", source=source, target=a)
    class OrderSplit:
        pass

    world.classes["OrderSplit"] = OrderSplit
    world.classes["__saga_targets__"] = [a, b]


@given(
    parsers.parse(
        'the saga handles OrderCreated by emitting a ReserveStock for "{inv}" and a CreateShipment for "{ful}"'
    )
)
def _given_saga_multi_handler(world, inv, ful):
    cls = world.classes["OrderSplit"]

    @handles(OrderCreated)
    def translate(self, event, destinations):
        return [
            command_book(
                ReserveStock(order_id=event.order_id, sku="x", quantity=1),
                target_domain=inv,
                seq=destinations.sequence_for(inv),
            ),
            command_book(
                CreateShipment(order_id=event.order_id, address="X"),
                target_domain=ful,
                seq=destinations.sequence_for(ful),
            ),
        ]

    cls.translate = translate
    world.handlers.append(cls())
    h = world.handlers[-1]
    world.router = Router("sagas").with_handler(type(h), lambda h=h: h).build()


@then(parsers.parse("the ReserveStock command carries destination sequence {n:d}"))
def _then_reserve_stock_seq(world, n):
    reserve = next(c for c in world.response.commands if c.cover.domain == "inventory")
    assert reserve.pages[0].header.sequence == n


@then(parsers.parse("the CreateShipment command carries destination sequence {n:d}"))
def _then_create_shipment_seq(world, n):
    shipment = next(
        c for c in world.response.commands if c.cover.domain == "fulfillment"
    )
    assert shipment.pages[0].header.sequence == n


# --------------------------------------------------------------------------
# Audit #86: edition propagation step impls (C-0138..C-0142).
# --------------------------------------------------------------------------


@given(parsers.parse('the source event has edition "{name}"'))
def _given_source_edition(world, name):
    world.source_edition = (name, [])


@given("the source event has no edition set")
def _given_source_no_edition(world):
    world.source_edition = None


@given(
    parsers.parse(
        'the source event has edition "{name}" with divergence at "{dom}"={seq:d}'
    )
)
def _given_source_edition_with_divergence(world, name, dom, seq):
    world.source_edition = (name, [(dom, seq)])


@given(parsers.parse('the saga handler sets outgoing edition "{outgoing}"'))
def _given_handler_sets_outgoing_edition(world, outgoing):
    """Audit #86 C-0140: re-bind the saga handler to set a non-empty
    edition on the outgoing CommandBook so we can verify the
    framework's always-override semantics."""
    cls = world.classes["OrderFulfillment"]

    @handles(OrderCreated)
    def translate(self, event, destinations):  # noqa: ARG001
        cb = command_book(
            ReserveStock(order_id=event.order_id, sku="sku-1", quantity=1),
            target_domain="inventory",
        )
        cb.cover.edition.name = outgoing
        return cb

    cls.translate = translate


# --- OrderAudit saga (C-0139): emits an EventBook fact instead of a command. ---


@given(parsers.parse('a saga "OrderAudit" translating from "order" to "audit"'))
def _given_audit_saga_class(world):
    @saga(name="saga-order-audit", source="order", target="audit")
    class OrderAudit:
        pass

    world.classes["OrderAudit"] = OrderAudit


@given("the saga handles OrderCreated by emitting an OrderObserved event")
def _given_audit_handler(world):
    cls = world.classes["OrderAudit"]

    @handles(OrderCreated)
    def translate(self, event, destinations):  # noqa: ARG001
        # Emit an EventBook fact-style with cover.domain="audit" and
        # no edition (framework will stamp source's edition).
        book = EventBook()
        book.cover.domain = "audit"
        return book

    cls.translate = translate


@given("the router is built with the OrderAudit saga")
def _given_audit_built(world):
    # Background already registered OrderFulfillment at handlers[0];
    # the audit instance is the most recently appended one.
    world.handlers.append(world.classes["OrderAudit"]())
    h = world.handlers[-1]
    world.router = Router("sagas").with_handler(type(h), lambda h=h: h).build()


# --- Then steps for emitted-cover edition assertions. ---


def _emitted_command_cover(world):
    return world.response.commands[0].cover


def _emitted_event_cover(world):
    return world.response.events[0].cover


@then(parsers.parse('the emitted command\'s cover has edition "{expected}"'))
def _then_command_edition(world, expected):
    cover = _emitted_command_cover(world)
    actual = cover.edition.name if cover.HasField("edition") else ""
    assert actual == expected


@then("the emitted command's cover has no edition set")
def _then_command_no_edition(world):
    cover = _emitted_command_cover(world)
    assert not cover.HasField("edition"), (
        f"expected no edition; got {cover.edition!r}"
    )


@then(parsers.parse('the emitted event\'s cover has edition "{expected}"'))
def _then_event_edition(world, expected):
    cover = _emitted_event_cover(world)
    actual = cover.edition.name if cover.HasField("edition") else ""
    assert actual == expected


@then(
    parsers.parse(
        'the emitted command\'s cover has edition "{name}" with divergence at "{dom}"={seq:d}'
    )
)
def _then_command_edition_with_divergence(world, name, dom, seq):
    cover = _emitted_command_cover(world)
    assert cover.HasField("edition")
    assert cover.edition.name == name
    found = next((d for d in cover.edition.divergences if d.domain == dom), None)
    assert found is not None, f"no divergence for domain {dom!r}"
    assert found.sequence == seq
