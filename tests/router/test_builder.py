"""R3: builder collects (class, factory) pairs.

Tests the ``Router(name).with_handler(cls, factory)`` shape — registration only.
Tests for mode inference (R4) and per-kind validation (R5) live in later files.

Covered here:
  - Router construction with name
  - with_handler appends a (cls, factory) tuple, returns self (fluent)
  - build() on empty router raises BuildError
  - with_handler on an undecorated class raises BuildError
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from angzarr_client.router import (
    command_handler,
    Router,
)
from angzarr_client.router.builder import BuildError


@dataclass
class PState:
    x: int = 0


@command_handler(domain="player", state=PState)
class Player:
    pass


def test_router_construction_stores_name():
    r = Router("agg-service")
    assert r.name == "agg-service"


def test_router_starts_empty():
    r = Router("x")
    assert r._factories == []


def test_with_handler_appends_factory():
    r = Router("x")
    factory = lambda: Player()  # noqa: E731
    r.with_handler(Player, factory)
    assert r._factories == [(Player, factory)]


def test_with_handler_returns_self_for_chaining():
    r = Router("x")
    assert r.with_handler(Player, lambda: Player()) is r


def test_with_handler_chain_registers_multiple():
    r = (
        Router("x")
        .with_handler(Player, lambda: Player())
        .with_handler(Player, lambda: Player())
    )
    assert len(r._factories) == 2


def test_build_on_empty_router_raises():
    with pytest.raises(BuildError, match="no handlers"):
        Router("empty").build()


def test_with_handler_rejects_undecorated_class():
    class NotDecorated:
        pass

    r = Router("x")
    with pytest.raises(BuildError, match="NotDecorated"):
        r.with_handler(NotDecorated, lambda: NotDecorated())


def test_with_handler_rejects_plain_object_class():
    r = Router("x")
    with pytest.raises(BuildError, match="no @command_handler"):
        r.with_handler(object, lambda: object())


def test_build_error_names_the_class():
    class MissingDecoration:
        pass

    with pytest.raises(BuildError) as exc_info:
        Router("x").with_handler(MissingDecoration, lambda: MissingDecoration()).build()
    assert "MissingDecoration" in str(exc_info.value)


def test_factory_is_stored_verbatim_not_called_at_registration():
    calls = []

    def factory():
        calls.append(None)
        return Player()

    Router("x").with_handler(Player, factory)
    assert calls == []
