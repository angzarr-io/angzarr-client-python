"""Step defs for features/multi_handler.feature."""

from __future__ import annotations

from dataclasses import dataclass

from pytest_bdd import given, parsers, scenarios, then, when

from angzarr_client.router import (
    ProcessManagerResponse,
    Router,
    applies,
    command_handler,
    handles,
    process_manager,
    projector,
    saga,
)
from tests.client.steps._helpers import (
    command_book,
    contextual_command,
    event_book,
    pm_request,
    saga_request,
)
from tests.fixtures import (
    CreateOrder,
    CreateShipment,
    OrderCompleted,
    OrderCreated,
    ReserveStock,
)

scenarios("multi_handler.feature")


@dataclass
class StateA:
    counter_a: int = 0


@dataclass
class StateB:
    counter_b: int = 0


@given(parsers.parse('two command handlers Alpha and Beta for domain "order"'))
def _given_alpha_beta(world):
    world.classes["Alpha"] = None  # placeholders filled by later @given steps
    world.classes["Beta"] = None
    world.classes["__alpha_emits_n__"] = 1
    world.classes["__beta_emits_n__"] = 1
    world.classes["__alpha_applier__"] = None
    world.classes["__beta_applier__"] = None


@given("Alpha handles CreateOrder by emitting OrderCreated")
def _given_alpha_handles_create_order(world):
    world.classes["__alpha_handler__"] = "OrderCreated"


@given("Beta handles CreateOrder by emitting OrderCompleted")
def _given_beta_handles_create_order(world):
    world.classes["__beta_handler__"] = "OrderCompleted"


def _build_alpha_beta(world):
    """Deferred Alpha/Beta construction once all configuration is in place."""
    call_log = world.call_log
    observed = world.observed

    alpha_emits = world.classes.get("__alpha_emits_n__", 1)
    beta_emits = world.classes.get("__beta_emits_n__", 1)
    alpha_applier = world.classes.get("__alpha_applier__")
    beta_applier = world.classes.get("__beta_applier__")

    @command_handler(domain="order", state=StateA)
    class Alpha:
        if alpha_applier is not None:

            @applies(alpha_applier)
            def apply(self, state, event):
                state.counter_a += 1

        @handles(CreateOrder)
        def on(self, cmd, state, seq):
            call_log.append("Alpha")
            observed["alpha_seq"] = seq
            observed["alpha_state"] = state.counter_a
            events = [OrderCreated(order_id=cmd.order_id) for _ in range(alpha_emits)]
            return events[0] if len(events) == 1 else tuple(events)

    @command_handler(domain="order", state=StateB)
    class Beta:
        if beta_applier is not None:

            @applies(beta_applier)
            def apply(self, state, event):
                state.counter_b += 1

        @handles(CreateOrder)
        def on(self, cmd, state, seq):
            call_log.append("Beta")
            observed["beta_seq"] = seq
            observed["beta_state"] = state.counter_b
            events = [OrderCompleted(order_id=cmd.order_id) for _ in range(beta_emits)]
            return events[0] if len(events) == 1 else tuple(events)

    world.classes["Alpha"] = Alpha
    world.classes["Beta"] = Beta
    world.handlers = [Alpha(), Beta()]
    h0, h1 = world.handlers[0], world.handlers[1]
    world.router = (
        Router("agg")
        .with_handler(type(h0), lambda h=h0: h)
        .with_handler(type(h1), lambda h=h1: h)
        .build()
    )


@given("the router is built with Alpha then Beta")
def _given_built_alpha_beta(world):
    if "alpha_calls" in world.observed:
        Alpha = world.classes["Alpha"]
        Beta = world.classes["Beta"]
        observed = world.observed

        def alpha_factory():
            observed["alpha_calls"] += 1
            return Alpha()

        def beta_factory():
            observed["beta_calls"] += 1
            return Beta()

        world.router = (
            Router("dupes")
            .with_handler(Alpha, alpha_factory)
            .with_handler(Beta, beta_factory)
            .build()
        )
    else:
        _build_alpha_beta(world)


# --- Scenario 1: both called, merged order ---


@when(parsers.parse('CreateOrder(order_id="{order_id}") is dispatched'))
def _when_create_order(world, order_id):
    world.response = world.router.dispatch(
        contextual_command(
            CreateOrder(order_id=order_id), domain="order", prior=world.prior_events
        )
    )


@then("Alpha was called before Beta")
def _then_call_order(world):
    idx_a = world.call_log.index("Alpha")
    idx_b = world.call_log.index("Beta")
    assert idx_a < idx_b


@then("the response contains two events in [OrderCreated, OrderCompleted] order")
def _then_events_ordered(world):
    pages = world.response.events.pages
    assert len(pages) == 2
    assert pages[0].event.type_url.endswith("OrderCreated")
    assert pages[1].event.type_url.endswith("OrderCompleted")


# --- Scenario 2: seq increments across handlers ---


@given(parsers.parse("the prior EventBook's next_sequence is {n:d}"))
def _given_next_seq(world, n):
    world.prior_events = event_book([], domain="order")
    world.prior_events.next_sequence = n


@given(parsers.parse("Alpha emits two events per call"))
def _given_alpha_two(world):
    world.classes["__alpha_emits_n__"] = 2


@given(parsers.parse("Beta emits one event per call"))
def _given_beta_one(world):
    world.classes["__beta_emits_n__"] = 1
    # Configuration is now complete — build the router.
    _build_alpha_beta(world)


@when("CreateOrder is dispatched")
def _when_dispatch_plain(world):
    world.response = world.router.dispatch(
        contextual_command(
            CreateOrder(order_id="o-1"), domain="order", prior=world.prior_events
        )
    )


@then(parsers.parse("Alpha observed seq = {n:d}"))
def _then_alpha_seq(world, n):
    assert world.observed["alpha_seq"] == n


@then(parsers.parse("Beta observed seq = {n:d}"))
def _then_beta_seq(world, n):
    assert world.observed["beta_seq"] == n


@then("the emitted pages carry sequences [5, 6, 7]")
def _then_merged_seqs(world):
    seqs = [p.header.sequence for p in world.response.events.pages]
    assert seqs == [5, 6, 7]


# --- Scenario 3: state isolation ---


@given("Alpha applies OrderCreated by incrementing counter_a")
def _given_alpha_applier(world):
    world.classes["__alpha_applier__"] = OrderCreated


@given("Beta applies OrderCompleted by incrementing counter_b")
def _given_beta_applier(world):
    world.classes["__beta_applier__"] = OrderCompleted


@given("a prior EventBook with [OrderCreated, OrderCreated, OrderCompleted]")
def _given_prior_mix(world):
    world.prior_events = event_book(
        [
            OrderCreated(order_id="a"),
            OrderCreated(order_id="b"),
            OrderCompleted(order_id="a"),
        ],
        domain="order",
    )
    # Build the router now that all gived-configuration is known.
    _build_alpha_beta(world)


@when("a command is dispatched")
def _when_dispatch_any(world):
    world.response = world.router.dispatch(
        contextual_command(
            CreateOrder(order_id="o-1"), domain="order", prior=world.prior_events
        )
    )


@then(parsers.parse("Alpha observed counter_a = {n:d}"))
def _then_alpha_counter(world, n):
    assert world.observed["alpha_state"] == n


@then(parsers.parse("Beta observed counter_b = {n:d}"))
def _then_beta_counter(world, n):
    assert world.observed["beta_state"] == n


# --------------------------------------------------------------------------
# Saga multi-handler (C-0013)
# --------------------------------------------------------------------------


@given('two sagas SagaA and SagaB both listening to source "order" for OrderCreated')
def _given_two_sagas(world):
    call_log = world.call_log

    @saga(name="saga-a", source="order", target="inventory")
    class SagaA:
        @handles(OrderCreated)
        def translate(self, event, destinations):
            call_log.append("SagaA")
            return command_book(
                ReserveStock(order_id=event.order_id, sku="x", quantity=1),
                target_domain="inventory",
            )

    @saga(name="saga-b", source="order", target="fulfillment")
    class SagaB:
        @handles(OrderCreated)
        def translate(self, event, destinations):
            call_log.append("SagaB")
            return command_book(
                CreateShipment(order_id=event.order_id, address="X"),
                target_domain="fulfillment",
            )

    world.classes["SagaA"] = SagaA
    world.classes["SagaB"] = SagaB


@given('SagaA emits a ReserveStock command for "inventory"')
@given('SagaB emits a CreateShipment command for "fulfillment"')
def _noop(world):
    """Intent already encoded in the class definitions above."""


@given("the saga router is built with SagaA then SagaB")
def _given_saga_router_built(world):
    SagaA, SagaB = world.classes["SagaA"], world.classes["SagaB"]
    world.router = (
        Router("sagas")
        .with_handler(SagaA, lambda: SagaA())
        .with_handler(SagaB, lambda: SagaB())
        .build()
    )


@when("an OrderCreated event is dispatched to the saga router")
def _when_saga_multi_dispatch(world):
    world.response = world.router.dispatch(
        saga_request([OrderCreated(order_id="o-1")], source_domain="order")
    )


@then("the response contains two commands in registration order")
def _then_two_commands_order(world):
    assert len(world.response.commands) == 2
    # PM tier asserts the same phrasing; the response shape differs.
    # For saga, commands are CommandBooks; call log records registration order.
    if world.call_log and world.call_log[0] in ("SagaA", "SagaB"):
        assert world.call_log[:2] == ["SagaA", "SagaB"]


@then('the first command targets the "inventory" domain')
def _then_first_inventory(world):
    assert world.response.commands[0].cover.domain == "inventory"


@then('the second command targets the "fulfillment" domain')
def _then_second_fulfillment(world):
    assert world.response.commands[1].cover.domain == "fulfillment"


# --------------------------------------------------------------------------
# Process manager multi-handler (C-0014)
# --------------------------------------------------------------------------


@dataclass
class _PMStateEmpty:
    pass


@given(
    'two process managers PMA and PMB both sourcing from "order" and handling OrderCreated'
)
def _given_two_pms(world):
    call_log = world.call_log

    @process_manager(
        name="pm-a",
        pm_domain="pm-a",
        sources=["order"],
        targets=["inventory"],
        state=_PMStateEmpty,
    )
    class PMA:
        @handles(OrderCreated)
        def on(self, event, state, destinations):
            call_log.append("PMA")
            return ProcessManagerResponse(
                commands=[
                    command_book(
                        ReserveStock(order_id=event.order_id, sku="x", quantity=1),
                        target_domain="inventory",
                    )
                ]
            )

    @process_manager(
        name="pm-b",
        pm_domain="pm-b",
        sources=["order"],
        targets=["fulfillment"],
        state=_PMStateEmpty,
    )
    class PMB:
        @handles(OrderCreated)
        def on(self, event, state, destinations):
            call_log.append("PMB")
            return ProcessManagerResponse(
                commands=[
                    command_book(
                        CreateShipment(order_id=event.order_id, address="X"),
                        target_domain="fulfillment",
                    )
                ]
            )

    world.classes["PMA"] = PMA
    world.classes["PMB"] = PMB


@given("PMA emits a ReserveStock command")
@given("PMB emits a CreateShipment command")
def _noop_pm(world):
    """Intent encoded in class definitions."""


@given("the PM router is built with PMA then PMB")
def _given_pm_router_built(world):
    PMA, PMB = world.classes["PMA"], world.classes["PMB"]
    world.router = (
        Router("pms")
        .with_handler(PMA, lambda: PMA())
        .with_handler(PMB, lambda: PMB())
        .build()
    )


@when("an OrderCreated trigger is dispatched to the PM router")
def _when_pm_multi_dispatch(world):
    world.response = world.router.dispatch(
        pm_request([OrderCreated(order_id="o-1")], source_domain="order")
    )


# --------------------------------------------------------------------------
# Projector multi-handler fan-out (C-0015)
# --------------------------------------------------------------------------


@given('two projectors ProjA and ProjB both consuming domain "order"')
def _given_two_projectors(world):
    log_a = world.observed.setdefault("log_a", [])
    log_b = world.observed.setdefault("log_b", [])

    @projector(name="prj-a", domains=["order"])
    class ProjA:
        @handles(OrderCreated)
        def on(self, event):
            log_a.append(event.order_id)

    @projector(name="prj-b", domains=["order"])
    class ProjB:
        @handles(OrderCreated)
        def on(self, event):
            log_b.append(event.order_id)

    world.classes["ProjA"] = ProjA
    world.classes["ProjB"] = ProjB


@given("ProjA appends to a log on OrderCreated")
@given("ProjB appends to a different log on OrderCreated")
def _noop_proj(world):
    """Intent encoded in class definitions."""


@given("the projector router is built with ProjA then ProjB")
def _given_proj_router_built(world):
    ProjA, ProjB = world.classes["ProjA"], world.classes["ProjB"]
    world.router = (
        Router("prjs")
        .with_handler(ProjA, lambda: ProjA())
        .with_handler(ProjB, lambda: ProjB())
        .build()
    )


@when("an EventBook with one OrderCreated event is dispatched")
def _when_one_event_dispatched(world):
    world.router.dispatch(event_book([OrderCreated(order_id="o-1")], domain="order"))


@then(parsers.parse("ProjA's log has {n:d} entry"))
def _then_log_a(world, n):
    assert len(world.observed["log_a"]) == n


@then(parsers.parse("ProjB's log has {n:d} entry"))
def _then_log_b(world, n):
    assert len(world.observed["log_b"]) == n


# --- Factory-invocation scenario (@C-0087) ---


@dataclass
class _OrderState:
    created: bool = False


@given(
    'two command handlers Alpha and Beta for domain "order" both handling CreateOrder'
)
def _given_alpha_beta_both_handle_create_order(world):
    OrderState = _OrderState

    @command_handler(domain="order", state=OrderState)
    class Alpha:
        @handles(CreateOrder)
        def handle(self, _cmd, _state, _seq):
            return None

    @command_handler(domain="order", state=OrderState)
    class Beta:
        @handles(CreateOrder)
        def handle(self, _cmd, _state, _seq):
            return None

    world.classes["Alpha"] = Alpha
    world.classes["Beta"] = Beta


@given("each factory counts invocations")
def _given_factories_count(world):
    world.observed["alpha_calls"] = 0
    world.observed["beta_calls"] = 0


@then(parsers.parse("Alpha's factory was invoked exactly {n:d} time"))
def _then_alpha_calls(world, n):
    assert world.observed["alpha_calls"] == n, world.observed["alpha_calls"]


@then(parsers.parse("Beta's factory was invoked exactly {n:d} time"))
def _then_beta_calls(world, n):
    assert world.observed["beta_calls"] == n, world.observed["beta_calls"]
