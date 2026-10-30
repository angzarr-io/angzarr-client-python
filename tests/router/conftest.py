"""Shared pytest fixtures for the router tests: a router hosting the tier's
CounterAggregate (tests/router/contract.py), and what its handler saw."""

from __future__ import annotations

import pytest

from angzarr_client.router import Router

from .contract import Observed, counter_aggregate


@pytest.fixture
def observed() -> list[Observed]:
    """What each IncreaseBy call saw."""
    return []


@pytest.fixture
def router(observed: list[Observed]):
    """A router hosting the CounterAggregate."""
    r = Router()
    r.register_aggregate(counter_aggregate(observed))
    yield r
    r.close()
