"""Step defs for features/client/validation.feature."""

from __future__ import annotations

from dataclasses import dataclass

from pytest_bdd import given, parsers, scenarios, then, when

from angzarr_client.router import (
    applies,
    command_handler,
    handles,
    process_manager,
    projector,
    saga,
)
from angzarr_client.router.builder import BuildError
from tests.fixtures import CreateOrder, OrderCreated

scenarios("validation.feature")

CONFIG_ERRORS = (TypeError, BuildError)


@dataclass
class _S:
    pass


@when(parsers.parse('I declare a @command_handler for domain "{domain}" without state'))
def _cmd_handler_missing_state(world, domain):
    try:
        @command_handler(domain=domain)  # type: ignore[call-arg]
        class X:
            pass
    except CONFIG_ERRORS as exc:
        world.dispatch_exc = exc


@when(parsers.parse('I declare a @saga named "{name}" from "{source}" without target'))
def _saga_missing_target(world, name, source):
    try:
        @saga(name=name, source=source)  # type: ignore[call-arg]
        class X:
            pass
    except CONFIG_ERRORS as exc:
        world.dispatch_exc = exc


@when(parsers.parse('I declare a @process_manager for name "{name}" without pm_domain'))
def _pm_missing_pm_domain(world, name):
    try:
        @process_manager(  # type: ignore[call-arg]
            name=name, sources=["a"], targets=["b"], state=_S
        )
        class X:
            pass
    except CONFIG_ERRORS as exc:
        world.dispatch_exc = exc


@when(parsers.parse('I declare a @process_manager for name "{name}" without sources'))
def _pm_missing_sources(world, name):
    try:
        @process_manager(  # type: ignore[call-arg]
            name=name, pm_domain="p", targets=["b"], state=_S
        )
        class X:
            pass
    except CONFIG_ERRORS as exc:
        world.dispatch_exc = exc


@when(parsers.parse('I declare a @process_manager for name "{name}" without targets'))
def _pm_missing_targets(world, name):
    try:
        @process_manager(  # type: ignore[call-arg]
            name=name, pm_domain="p", sources=["a"], state=_S
        )
        class X:
            pass
    except CONFIG_ERRORS as exc:
        world.dispatch_exc = exc


@when(parsers.parse('I declare a @projector named "{name}" without domains'))
def _projector_missing_domains(world, name):
    try:
        @projector(name=name)  # type: ignore[call-arg]
        class X:
            pass
    except CONFIG_ERRORS as exc:
        world.dispatch_exc = exc


@given("a class Order")
def _given_bare_class(world):
    class Order:
        pass

    world.classes["Order"] = Order


@given("Order has the @command_handler decorator applied")
def _apply_cmd_handler(world):
    world.classes["Order"] = command_handler(domain="order", state=_S)(
        world.classes["Order"]
    )


@when("I also apply the @saga decorator to Order")
def _apply_second_decorator(world):
    try:
        saga(name="x", source="a", target="b")(world.classes["Order"])
    except CONFIG_ERRORS as exc:
        world.dispatch_exc = exc


@given(parsers.parse('a method "{name}" with the @handles decorator applied'))
def _given_method_with_handles(world, name):
    def handle(self, cmd, state):  # noqa: ARG001
        return None

    handle.__name__ = name
    world.classes[name] = handles(CreateOrder)(handle)


@when(parsers.parse('I also apply the @applies decorator to "{name}"'))
def _also_apply_applies(world, name):
    try:
        applies(OrderCreated)(world.classes[name])
    except CONFIG_ERRORS as exc:
        world.dispatch_exc = exc


@then("the declaration raises a configuration error")
def _then_config_error(world):
    assert isinstance(world.dispatch_exc, CONFIG_ERRORS), (
        f"expected TypeError or BuildError, got {type(world.dispatch_exc).__name__}"
    )
