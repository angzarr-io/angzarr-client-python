"""Step defs for features/client/builder.feature."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from angzarr_client.router import Router, command_handler, handles, saga
from angzarr_client.router.builder import BuildError
from angzarr_client.router.runtime import CommandHandlerRouter, SagaRouter
from tests.fixtures import CreateOrder

scenarios("builder.feature")


@dataclass
class OrderState:
    pass


@dataclass
class PaymentState:
    pass


@given(parsers.parse('an empty Router named "{name}"'))
def _given_empty_router(world, name):
    world.classes["__router__"] = Router(name)


@given(parsers.parse('a class "{name}" with no kind decorator'))
def _given_undecorated_class(world, name):
    class NotDecorated:
        pass

    NotDecorated.__name__ = name
    world.classes[name] = NotDecorated


@given(
    parsers.parse(
        'a command handler "{name}" for domain "{domain}" with state {state_name}'
    )
)
def _given_command_handler(world, name, domain, state_name):
    state_cls = {"OrderState": OrderState, "PaymentState": PaymentState}[state_name]

    @command_handler(domain=domain, state=state_cls)
    class Handler:
        pass

    Handler.__name__ = name
    world.classes[name] = Handler
    router = world.classes.get("__router__") or Router("test")
    world.classes["__router__"] = router.with_handler(
        Handler, lambda cls=Handler: cls()
    )


@given(
    parsers.parse(
        'another command handler "{name}" for domain "{domain}" with state {state_name}'
    )
)
def _given_another_command_handler(world, name, domain, state_name):
    _given_command_handler(world, name, domain, state_name)


@given(parsers.parse('a saga "{name}" translating from "{source}" to "{target}"'))
def _given_saga(world, name, source, target):
    @saga(name=name, source=source, target=target)
    class Saga:
        @handles(CreateOrder)
        def handle(self, _event):
            return None

    Saga.__name__ = name
    world.classes[name] = Saga
    router = world.classes.get("__router__") or Router("test")
    world.classes["__router__"] = router.with_handler(Saga, lambda cls=Saga: cls())


@given(
    'two command handlers Alpha and Beta for domain "order" both handling CreateOrder'
)
def _given_two_duplicate_handlers(world):
    @command_handler(domain="order", state=OrderState)
    class Alpha:
        @handles(CreateOrder)
        def handle(self, _cmd, _state):
            return None

    @command_handler(domain="order", state=OrderState)
    class Beta:
        @handles(CreateOrder)
        def handle(self, _cmd, _state):
            return None

    world.classes["Alpha"] = Alpha
    world.classes["Beta"] = Beta
    world.classes["__router__"] = (
        Router("dupes")
        .with_handler(Alpha, lambda: Alpha())
        .with_handler(Beta, lambda: Beta())
    )


@given("a factory that counts invocations")
def _given_counting_factory(world):
    cls = world.classes["Order"]
    world.observed["factory_calls"] = 0

    def factory():
        world.observed["factory_calls"] += 1
        return cls()

    world.classes["__counting_factory__"] = factory


@when("I build the router")
def _when_build(world):
    try:
        world.response = world.classes["__router__"].build()
    except BuildError as exc:
        world.dispatch_exc = exc


@when("I register it with a factory")
def _when_register_undecorated(world):
    cls = next(v for k, v in world.classes.items() if k != "__router__")
    router = Router("x")
    try:
        router.with_handler(cls, lambda: cls())
    except BuildError as exc:
        world.dispatch_exc = exc


@when("I register the handler and build the router")
def _when_register_and_build_counting(world):
    handler_cls = world.classes["Order"]
    factory = world.classes["__counting_factory__"]
    Router("x").with_handler(handler_cls, factory).build()


@then(parsers.parse('the builder raises a BuildError mentioning "{needle}"'))
def _then_build_error(world, needle):
    # Audit #72: BuildError now carries a static `message` plus
    # structured `details` (handler class name, field, conflicting
    # kinds, etc.). Cucumber needles like "NotDecorated" or "cannot
    # mix" land in either the message or one of the detail values
    # depending on the case, so search both.
    assert isinstance(world.dispatch_exc, BuildError), world.dispatch_exc
    err = world.dispatch_exc
    haystack = str(err) + " " + " ".join(err.details.values())
    assert needle in haystack, (needle, str(err), err.details)


@then("the result is a CommandHandlerRouter")
def _then_command_handler_router(world):
    assert world.dispatch_exc is None, world.dispatch_exc
    assert isinstance(world.response, CommandHandlerRouter)


@then("the result is a SagaRouter")
def _then_saga_router(world):
    assert world.dispatch_exc is None, world.dispatch_exc
    assert isinstance(world.response, SagaRouter)


@then("the build succeeds")
def _then_build_succeeds(world):
    assert world.dispatch_exc is None, world.dispatch_exc


@then("the router has two factories registered")
def _then_two_factories(world):
    assert len(world.classes["__router__"]._factories) == 2


@then(parsers.parse("the factory invocation count is {n:d}"))
def _then_factory_call_count(world, n):
    assert world.observed["factory_calls"] == n


@pytest.fixture(autouse=True)
def _reset_call_log(world):
    world.dispatch_exc = None
    world.response = None
    yield


# ---------------------------------------------------------------------------
# WIP — Batch 6/7 new business-vocab phrasing
# Stubs raise NotImplementedError so unimplemented vocabulary fails loudly
# rather than passing silently. Replace each as the proper impl lands.
# ---------------------------------------------------------------------------


# TODO (WIP): Implement this step matcher properly.
@given("an empty handler configuration")
def _given_empty_handler_config(world):
    raise NotImplementedError("WIP: step needs implementation")


# TODO (WIP): Implement this step matcher properly.
@given("a component that has not been marked as a handler kind")
def _given_unmarked_component(world):
    raise NotImplementedError("WIP: step needs implementation")


# TODO (WIP): Implement this step matcher properly.
@given(
    parsers.parse('a command handler "{name}" for domain "{domain}" with order state')
)
def _given_command_handler_with_order_state(world, name, domain):
    raise NotImplementedError("WIP: step needs implementation")


# TODO (WIP): Implement this step matcher properly.
@given(
    parsers.parse(
        'another command handler "{name}" for domain "{domain}" with payment state'
    )
)
def _given_another_command_handler_payment_state(world, name, domain):
    raise NotImplementedError("WIP: step needs implementation")


# TODO (WIP): Implement this step matcher properly.
@given(
    'two command handlers Alpha and Beta for domain "order" both handling '
    "the same command"
)
def _given_two_handlers_same_command(world):
    raise NotImplementedError("WIP: step needs implementation")


# TODO (WIP): Implement this step matcher properly.
@given("the handler reports how many times it has been introspected")
def _given_introspection_counter(world):
    raise NotImplementedError("WIP: step needs implementation")


# TODO (WIP): Implement this step matcher properly.
@when("I attempt to register it")
def _when_attempt_register(world):
    raise NotImplementedError("WIP: step needs implementation")


# TODO (WIP): Implement this step matcher properly.
@then("the configuration is rejected because no handlers are registered")
def _then_rejected_no_handlers(world):
    raise NotImplementedError("WIP: step needs implementation")


# TODO (WIP): Implement this step matcher properly.
@then("the configuration is rejected because the component is not a recognised handler")
def _then_rejected_not_recognised(world):
    raise NotImplementedError("WIP: step needs implementation")


# TODO (WIP): Implement this step matcher properly.
@then("the result routes commands to their handlers")
def _then_routes_commands(world):
    raise NotImplementedError("WIP: step needs implementation")


# TODO (WIP): Implement this step matcher properly.
@then("the configuration is rejected for mixing handler kinds")
def _then_rejected_mixed_kinds(world):
    raise NotImplementedError("WIP: step needs implementation")


# TODO (WIP): Implement this step matcher properly.
@then(
    "the configuration is rejected because two command handlers share "
    "the same domain and command"
)
def _then_rejected_shared_pair(world):
    raise NotImplementedError("WIP: step needs implementation")


# TODO (WIP): Implement this step matcher properly.
@then("the handler has been introspected exactly once")
def _then_introspected_once(world):
    raise NotImplementedError("WIP: step needs implementation")


# TODO (WIP): Implement this step matcher properly.
@then("the result routes saga notifications to their handlers")
def _then_routes_saga_notifications(world):
    raise NotImplementedError("WIP: step needs implementation")
