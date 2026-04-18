"""R1: class decorators stash metadata on the class.

Tests the four class-level decorators:
    @command_handler(domain, state)
    @saga(name, source, target)
    @process_manager(name, pm_domain, sources, targets, state)
    @projector(name, domains)

Each sets a `__angzarr_kind__` attribute identifying the component kind, and
a `__angzarr_meta__` dict with the kwargs. Stacking two kind-decorators on one
class raises TypeError.
"""

from dataclasses import dataclass

import pytest

from angzarr_client.router.decorators import (
    command_handler,
    process_manager,
    projector,
    saga,
)


@dataclass
class PlayerState:
    player_id: str = ""


@dataclass
class FulfillmentState:
    pending: int = 0


# --------------------------------------------------------------------------
# @command_handler
# --------------------------------------------------------------------------


def test_command_handler_sets_kind():
    @command_handler(domain="player", state=PlayerState)
    class Player:
        pass

    assert Player.__angzarr_kind__ == "command_handler"


def test_command_handler_stores_domain_and_state():
    @command_handler(domain="player", state=PlayerState)
    class Player:
        pass

    assert Player.__angzarr_meta__["domain"] == "player"
    assert Player.__angzarr_meta__["state"] is PlayerState


def test_command_handler_requires_domain():
    with pytest.raises(TypeError):

        @command_handler(state=PlayerState)  # type: ignore[call-arg]
        class _Missing:
            pass


def test_command_handler_requires_state():
    with pytest.raises(TypeError):

        @command_handler(domain="player")  # type: ignore[call-arg]
        class _Missing:
            pass


# --------------------------------------------------------------------------
# @saga
# --------------------------------------------------------------------------


def test_saga_sets_kind_and_meta():
    @saga(name="saga-order-fulfillment", source="order", target="inventory")
    class OrderFulfillment:
        pass

    assert OrderFulfillment.__angzarr_kind__ == "saga"
    assert OrderFulfillment.__angzarr_meta__["name"] == "saga-order-fulfillment"
    assert OrderFulfillment.__angzarr_meta__["source"] == "order"
    assert OrderFulfillment.__angzarr_meta__["target"] == "inventory"


def test_saga_requires_all_fields():
    with pytest.raises(TypeError):

        @saga(name="s", source="order")  # type: ignore[call-arg]
        class _MissingTarget:
            pass


# --------------------------------------------------------------------------
# @process_manager
# --------------------------------------------------------------------------


def test_process_manager_sets_kind_and_meta():
    @process_manager(
        name="pm-fulfillment",
        pm_domain="fulfillment",
        sources=["order", "inventory"],
        targets=["shipping"],
        state=FulfillmentState,
    )
    class Fulfillment:
        pass

    m = Fulfillment.__angzarr_meta__
    assert Fulfillment.__angzarr_kind__ == "process_manager"
    assert m["name"] == "pm-fulfillment"
    assert m["pm_domain"] == "fulfillment"
    assert m["sources"] == ["order", "inventory"]
    assert m["targets"] == ["shipping"]
    assert m["state"] is FulfillmentState


def test_process_manager_requires_all_fields():
    with pytest.raises(TypeError):

        @process_manager(name="pm", pm_domain="d", sources=[], targets=[])  # type: ignore[call-arg]
        class _MissingState:
            pass


# --------------------------------------------------------------------------
# @projector
# --------------------------------------------------------------------------


def test_projector_sets_kind_and_meta():
    @projector(name="prj-output", domains=["order", "hand"])
    class Output:
        pass

    assert Output.__angzarr_kind__ == "projector"
    assert Output.__angzarr_meta__["name"] == "prj-output"
    assert Output.__angzarr_meta__["domains"] == ["order", "hand"]


def test_projector_requires_both_fields():
    with pytest.raises(TypeError):

        @projector(name="prj")  # type: ignore[call-arg]
        class _MissingDomains:
            pass


# --------------------------------------------------------------------------
# Stacking kind decorators is an error
# --------------------------------------------------------------------------


def test_stacking_two_kind_decorators_raises():
    with pytest.raises(TypeError, match="already decorated"):

        @saga(name="s", source="a", target="b")
        @command_handler(domain="player", state=PlayerState)
        class _Both:
            pass


def test_stacking_pm_on_projector_raises():
    with pytest.raises(TypeError, match="already decorated"):

        @process_manager(
            name="pm", pm_domain="d", sources=["a"], targets=["b"], state=PlayerState
        )
        @projector(name="prj", domains=["a"])
        class _Both:
            pass


def test_stacking_same_kind_decorator_twice_raises():
    with pytest.raises(TypeError, match=r"already decorated with @projector"):

        @projector(name="prj", domains=["a"])
        @projector(name="prj", domains=["a"])
        class _Twice:
            pass


def test_stacking_same_command_handler_twice_raises():
    with pytest.raises(TypeError, match=r"already decorated with @command_handler"):

        @command_handler(domain="player", state=PlayerState)
        @command_handler(domain="player", state=PlayerState)
        class _Twice:
            pass


# --------------------------------------------------------------------------
# Decorators preserve the class (return the same object, allow normal use)
# --------------------------------------------------------------------------


def test_command_handler_returns_same_class():
    class Raw:
        x = 1

    Decorated = command_handler(domain="p", state=PlayerState)(Raw)
    assert Decorated is Raw
    assert Decorated.x == 1  # type: ignore[attr-defined]


def test_instance_carries_metadata_through_class():
    @command_handler(domain="player", state=PlayerState)
    class Player:
        pass

    inst = Player()
    assert type(inst).__angzarr_kind__ == "command_handler"
    assert type(inst).__angzarr_meta__["domain"] == "player"
