"""Shared pytest-bdd fixtures.

Each scenario gets a fresh ``World`` carrying router-under-build state, the
built router, captured dispatch output, and side-effect logs. Step defs in
``steps/*.py`` read and mutate ``world`` via the fixture.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from tests.client.wip_scenarios import WIP_SCENARIOS


def pytest_collection_modifyitems(config, items):
    """Mark scenarios with TODO-stub matchers as skip.

    Replaces the @wip gherkin-tag mechanism (which baked harness state
    into the .feature spec). See tests/client/wip_scenarios.py for the
    maintained skiplist. Iterates collected pytest items, finds the
    pytest-bdd Scenario object attached to each, and applies
    `pytest.mark.skip` when its (feature_basename, scenario_name) is
    in WIP_SCENARIOS.
    """
    skip_marker = pytest.mark.skip(
        reason="WIP: matcher implementation pending; see tests/client/wip_scenarios.py"
    )
    for item in items:
        scen = getattr(getattr(item, "function", None), "__scenario__", None)
        if scen is None:
            continue
        feature_basename = scen.feature.filename.rsplit("/", 1)[-1]
        if (feature_basename, scen.name) in WIP_SCENARIOS:
            item.add_marker(skip_marker)


@dataclass
class World:
    """Per-scenario test state shared across steps."""

    # Handler instances registered so far.
    handlers: list[Any] = field(default_factory=list)
    # Dynamic handler classes keyed by role name ("Order", "Alpha", ...).
    classes: dict[str, type] = field(default_factory=dict)
    # Built router (set after `the router is built ...` step).
    router: Any = None
    # Dispatch inputs / outputs.
    prior_events: Any = None
    response: Any = None
    dispatch_exc: Exception | None = None
    # Multi-handler call-order log.
    call_log: list[str] = field(default_factory=list)
    # Projector write log.
    write_log: list[Any] = field(default_factory=list)
    # Destination sequences observed by saga/PM handlers.
    dest_seqs: dict[str, int] = field(default_factory=dict)
    observed_dest: dict[str, int] = field(default_factory=dict)
    # Observed state fields during dispatch.
    observed: dict[str, Any] = field(default_factory=dict)


@pytest.fixture
def world() -> World:
    return World()


# --------------------------------------------------------------------------
# Shared step defs usable from any feature file
# --------------------------------------------------------------------------

from pytest_bdd import parsers, then  # noqa: E402


@then("the response contains exactly one command")
def _then_one_command(world):
    assert len(world.response.commands) == 1


@then("the response contains no commands")
def _then_no_commands(world):
    assert len(world.response.commands) == 0


@then(parsers.parse('the command targets the "{domain}" domain'))
def _then_command_targets(world, domain):
    assert world.response.commands[0].cover.domain == domain
