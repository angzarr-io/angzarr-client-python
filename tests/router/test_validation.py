"""R5: build-time validation per kind.

R1 already covers *missing* required fields (TypeError from keyword-only args).
R5 covers *semantic* invariants — empty strings, empty lists, and the
"call both" allowance for duplicate (domain, type_url) pairs.

Covered here:
  - Empty domain / name / source / target strings → BuildError at build time
  - Empty sources/targets/domains lists → BuildError
  - Duplicate (domain, command-type) across two instances is ALLOWED (call-both)
  - Duplicate (domain, event-type) across two instances is ALLOWED (call-both)
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from angzarr_client.router import (
    BuildError,
    Router,
    applies,
    command_handler,
    handles,
    process_manager,
    projector,
    saga,
)
from tests.fixtures import CreateOrder, OrderCreated


@dataclass
class S:
    x: int = 0


# --------------------------------------------------------------------------
# Semantic validation: empty values
# --------------------------------------------------------------------------


def test_empty_domain_on_command_handler_rejected_at_build():
    @command_handler(domain="", state=S)
    class BadPlayer:
        @handles(CreateOrder)
        def _h(self, cmd, state, seq):
            pass

    with pytest.raises(BuildError, match="domain"):
        Router("x").with_handler(BadPlayer()).build()


def test_empty_saga_target_rejected_at_build():
    @saga(name="bad", source="order", target="")
    class BadSaga:
        @handles(OrderCreated)
        def _h(self, evt, dests):
            pass

    with pytest.raises(BuildError, match="target"):
        Router("x").with_handler(BadSaga()).build()


def test_empty_saga_source_rejected_at_build():
    @saga(name="bad", source="", target="inventory")
    class BadSaga:
        @handles(OrderCreated)
        def _h(self, evt, dests):
            pass

    with pytest.raises(BuildError, match="source"):
        Router("x").with_handler(BadSaga()).build()


def test_empty_pm_sources_list_rejected_at_build():
    @process_manager(name="bad", pm_domain="f", sources=[], targets=["s"], state=S)
    class BadPM:
        @handles(OrderCreated)
        def _h(self, evt, state, dests):
            pass

    with pytest.raises(BuildError, match="sources"):
        Router("x").with_handler(BadPM()).build()


def test_empty_pm_targets_list_rejected_at_build():
    @process_manager(name="bad", pm_domain="f", sources=["order"], targets=[], state=S)
    class BadPM:
        @handles(OrderCreated)
        def _h(self, evt, state, dests):
            pass

    with pytest.raises(BuildError, match="targets"):
        Router("x").with_handler(BadPM()).build()


def test_empty_projector_domains_list_rejected_at_build():
    @projector(name="bad", domains=[])
    class BadProjector:
        @handles(OrderCreated)
        def _h(self, evt):
            pass

    with pytest.raises(BuildError, match="domains"):
        Router("x").with_handler(BadProjector()).build()


# --------------------------------------------------------------------------
# "Call both" — duplicate (domain, type) ACROSS instances is allowed
# --------------------------------------------------------------------------


def test_two_command_handlers_same_domain_same_type_allowed():
    """Per design: multiple handlers for the same (domain, type) are supported.

    Invocation order is registration order; emitted events merged across handlers.
    (R8 tests the merge behavior; here we only assert build succeeds.)
    """

    @command_handler(domain="player", state=S)
    class HandlerA:
        @applies(OrderCreated)
        def apply(self, state, evt):
            pass

        @handles(CreateOrder)
        def on(self, cmd, state, seq):
            pass

    @command_handler(domain="player", state=S)
    class HandlerB:
        @applies(OrderCreated)
        def apply(self, state, evt):
            pass

        @handles(CreateOrder)
        def on(self, cmd, state, seq):
            pass

    router = Router("x").with_handler(HandlerA()).with_handler(HandlerB()).build()
    assert len(router.handlers) == 2


def test_two_sagas_same_source_same_event_allowed():
    @saga(name="s1", source="order", target="inventory")
    class SA:
        @handles(OrderCreated)
        def on(self, evt, dests):
            pass

    @saga(name="s2", source="order", target="payment")
    class SB:
        @handles(OrderCreated)
        def on(self, evt, dests):
            pass

    router = Router("x").with_handler(SA()).with_handler(SB()).build()
    assert len(router.handlers) == 2


def test_two_projectors_same_domain_same_event_allowed():
    @projector(name="p1", domains=["order"])
    class PA:
        @handles(OrderCreated)
        def on(self, evt):
            pass

    @projector(name="p2", domains=["order"])
    class PB:
        @handles(OrderCreated)
        def on(self, evt):
            pass

    router = Router("x").with_handler(PA()).with_handler(PB()).build()
    assert len(router.handlers) == 2


# --------------------------------------------------------------------------
# A valid spec should NOT raise
# --------------------------------------------------------------------------


def test_valid_command_handler_builds_without_error():
    @command_handler(domain="player", state=S)
    class Valid:
        @handles(CreateOrder)
        def on(self, cmd, state, seq):
            pass

    router = Router("x").with_handler(Valid()).build()
    assert router is not None


def test_valid_saga_builds_without_error():
    @saga(name="s", source="order", target="inventory")
    class Valid:
        @handles(OrderCreated)
        def on(self, evt, dests):
            pass

    router = Router("x").with_handler(Valid()).build()
    assert router is not None


def test_valid_pm_builds_without_error():
    @process_manager(
        name="pm", pm_domain="f", sources=["order"], targets=["shipping"], state=S
    )
    class Valid:
        @handles(OrderCreated)
        def on(self, evt, state, dests):
            pass

    router = Router("x").with_handler(Valid()).build()
    assert router is not None


def test_valid_projector_builds_without_error():
    @projector(name="p", domains=["order"])
    class Valid:
        @handles(OrderCreated)
        def on(self, evt):
            pass

    router = Router("x").with_handler(Valid()).build()
    assert router is not None
