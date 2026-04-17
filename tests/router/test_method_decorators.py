"""R2: method decorators stash metadata on methods.

Tests the four method-level decorators:
    @handles(message_type)     — command or event dispatch registration
    @applies(event_type)       — event applier for state rebuild
    @rejected(domain, command) — rejection compensation handler
    @state_factory             — override for state factory construction

Each decorator sets a distinct sentinel attribute on the decorated callable.
Stacking conflicting decorators on one method raises TypeError.
"""

from __future__ import annotations

import pytest

from angzarr_client.router.decorators import (
    applies,
    handles,
    rejected,
    state_factory,
)
from tests.fixtures import (
    CreateOrder,
    OrderCreated,
    OrderCompleted,
    PlayerRegistered,
)

# --------------------------------------------------------------------------
# @handles
# --------------------------------------------------------------------------


def test_handles_sets_attribute():
    @handles(CreateOrder)
    def register(self, cmd, state, seq):
        return cmd

    assert register.__angzarr_handles__ is CreateOrder


def test_handles_preserves_function():
    @handles(CreateOrder)
    def register(self, cmd, state, seq):
        return "ok"

    # Still callable (not transformed)
    assert register(None, None, None, 0) == "ok"


def test_handles_returns_same_function_object():
    def raw(self, cmd, state, seq):
        return cmd

    wrapped = handles(CreateOrder)(raw)
    assert wrapped is raw


# --------------------------------------------------------------------------
# @applies
# --------------------------------------------------------------------------


def test_applies_sets_attribute():
    @applies(OrderCreated)
    def apply_created(self, state, event):
        pass

    assert apply_created.__angzarr_applies__ is OrderCreated


def test_applies_returns_same_function_object():
    def raw(self, state, event):
        pass

    wrapped = applies(OrderCreated)(raw)
    assert wrapped is raw


# --------------------------------------------------------------------------
# @rejected
# --------------------------------------------------------------------------


def test_rejected_sets_tuple_attribute():
    @rejected("payment", "ProcessPayment")
    def on_payment_rejected(self, notif, state):
        return None

    assert on_payment_rejected.__angzarr_rejected__ == ("payment", "ProcessPayment")


def test_rejected_requires_domain_and_command():
    with pytest.raises(TypeError):

        @rejected("payment")  # type: ignore[call-arg]
        def _bad(self, notif, state):
            pass


# --------------------------------------------------------------------------
# @state_factory
# --------------------------------------------------------------------------


def test_state_factory_sets_marker():
    @state_factory
    def empty(self):
        return {}

    assert empty.__angzarr_state_factory__ is True


def test_state_factory_preserves_function():
    @state_factory
    def empty(self):
        return {"seeded": True}

    assert empty(None) == {"seeded": True}


# --------------------------------------------------------------------------
# Stacking conflicting method decorators
# --------------------------------------------------------------------------


def test_cannot_stack_handles_and_applies():
    with pytest.raises(TypeError, match="already"):

        @handles(CreateOrder)
        @applies(OrderCreated)
        def _method(self, *args):
            pass


def test_cannot_stack_handles_and_rejected():
    with pytest.raises(TypeError, match="already"):

        @handles(CreateOrder)
        @rejected("payment", "ProcessPayment")
        def _method(self, *args):
            pass


def test_cannot_stack_state_factory_and_handles():
    with pytest.raises(TypeError, match="already"):

        @state_factory
        @handles(CreateOrder)
        def _method(self, *args):
            pass


# --------------------------------------------------------------------------
# Multiple handlers for different types on the same class coexist
# --------------------------------------------------------------------------


def test_multiple_handles_on_same_class_coexist():
    class H:
        @handles(CreateOrder)
        def on_create(self, cmd, state, seq):
            pass

        @handles(OrderCompleted)
        def on_complete(self, cmd, state, seq):
            pass

    assert H.on_create.__angzarr_handles__ is CreateOrder
    assert H.on_complete.__angzarr_handles__ is OrderCompleted


def test_multiple_applies_on_same_class_coexist():
    class H:
        @applies(OrderCreated)
        def a1(self, state, event):
            pass

        @applies(OrderCompleted)
        def a2(self, state, event):
            pass

        @applies(PlayerRegistered)
        def a3(self, state, event):
            pass

    assert H.a1.__angzarr_applies__ is OrderCreated
    assert H.a2.__angzarr_applies__ is OrderCompleted
    assert H.a3.__angzarr_applies__ is PlayerRegistered


def test_multiple_rejected_on_same_class_coexist():
    class H:
        @rejected("payment", "ProcessPayment")
        def r1(self, notif, state):
            pass

        @rejected("inventory", "ReserveStock")
        def r2(self, notif, state):
            pass

    assert H.r1.__angzarr_rejected__ == ("payment", "ProcessPayment")
    assert H.r2.__angzarr_rejected__ == ("inventory", "ReserveStock")
