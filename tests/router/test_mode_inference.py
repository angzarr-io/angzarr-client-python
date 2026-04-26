"""R4: mode inference returns the correct typed runtime router.

``Router.build()`` inspects the ``__angzarr_kind__`` of each registered
handler's class and returns:
  - CommandHandlerRouter when all handlers are @command_handler
  - SagaRouter when all handlers are @saga
  - ProcessManagerRouter when all handlers are @process_manager
  - ProjectorRouter when all handlers are @projector

Mixing kinds raises BuildError naming the offending kinds.

R4 only stands up empty stub runtime classes and the inference logic. Dispatch
behavior is covered in R6+.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from angzarr_client.router import (
    BuildError,
    Router,
    command_handler,
    process_manager,
    projector,
    saga,
)
from angzarr_client.router.runtime import (
    CommandHandlerRouter,
    ProcessManagerRouter,
    ProjectorRouter,
    SagaRouter,
)


@dataclass
class S:
    n: int = 0


@command_handler(domain="player", state=S)
class Player:
    pass


@saga(name="saga-x", source="order", target="inventory")
class OrderSaga:
    pass


@process_manager(
    name="pm-x",
    pm_domain="fulfillment",
    sources=["order"],
    targets=["shipping"],
    state=S,
)
class PM:
    pass


@projector(name="prj-x", domains=["order"])
class Output:
    pass


# --------------------------------------------------------------------------
# Happy paths: homogeneous handlers → typed runtime router
# --------------------------------------------------------------------------


def test_build_single_command_handler_returns_command_handler_router():
    router = Router("x").with_handler(Player, lambda: Player()).build()
    assert isinstance(router, CommandHandlerRouter)


def test_build_multiple_command_handlers_returns_command_handler_router():
    router = (
        Router("x").with_handler(Player, Player).with_handler(Player, Player).build()
    )
    assert isinstance(router, CommandHandlerRouter)


def test_handler_count_returns_number_of_factories():
    """P3.2 / audit finding: parity with Rust's `handler_count()`
    on every kind-specific runtime router (`runtime.rs:454-468`)."""
    one = Router("x").with_handler(Player, lambda: Player()).build()
    assert one.handler_count() == 1
    assert len(one) == 1  # __len__ alias

    three = (
        Router("x")
        .with_handler(Player, Player)
        .with_handler(Player, Player)
        .with_handler(Player, Player)
        .build()
    )
    assert three.handler_count() == 3
    assert len(three) == 3


def test_build_saga_returns_saga_router():
    router = Router("x").with_handler(OrderSaga, lambda: OrderSaga()).build()
    assert isinstance(router, SagaRouter)


def test_build_process_manager_returns_pm_router():
    router = Router("x").with_handler(PM, lambda: PM()).build()
    assert isinstance(router, ProcessManagerRouter)


def test_build_projector_returns_projector_router():
    router = Router("x").with_handler(Output, lambda: Output()).build()
    assert isinstance(router, ProjectorRouter)


# --------------------------------------------------------------------------
# Mixed kinds: BuildError
# --------------------------------------------------------------------------


def test_mixing_command_handler_and_saga_raises():
    r = (
        Router("x")
        .with_handler(Player, Player)
        .with_handler(OrderSaga, lambda: OrderSaga())
    )
    with pytest.raises(BuildError, match="cannot mix"):
        r.build()


def test_mixing_saga_and_pm_raises():
    r = (
        Router("x")
        .with_handler(OrderSaga, lambda: OrderSaga())
        .with_handler(PM, lambda: PM())
    )
    with pytest.raises(BuildError, match="cannot mix"):
        r.build()


def test_mixing_projector_and_command_handler_raises():
    r = Router("x").with_handler(Output, lambda: Output()).with_handler(Player, Player)
    with pytest.raises(BuildError, match="cannot mix"):
        r.build()


def test_build_error_names_both_kinds():
    r = (
        Router("x")
        .with_handler(Player, Player)
        .with_handler(OrderSaga, lambda: OrderSaga())
    )
    with pytest.raises(BuildError) as exc_info:
        r.build()
    msg = str(exc_info.value)
    assert "command_handler" in msg
    assert "saga" in msg


# --------------------------------------------------------------------------
# Built router exposes its name + registered factories
# --------------------------------------------------------------------------


def test_built_router_carries_name():
    router = Router("agg-service").with_handler(Player, lambda: Player()).build()
    assert router.name == "agg-service"


def test_built_router_carries_factories_in_registration_order():
    def f1():
        return Player()

    def f2():
        return Player()

    router = Router("x").with_handler(Player, f1).with_handler(Player, f2).build()
    assert router._factories == [(Player, f1), (Player, f2)]
