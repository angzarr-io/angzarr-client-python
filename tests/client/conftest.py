"""pytest configuration for the client-surface and parity feature tiers.

Scenarios the client cannot satisfy yet are strict xfail with the reason, so
one that starts passing fails the run until it is taken off this list.
"""

from __future__ import annotations

import pytest

from tests.conftest import SCENARIO_IDS_ATTR

PENDING = {
    "C-0336": "angzarr_client exposes no connect-timeout option on connect()",
    "C-0337": "angzarr_client exposes no keep-alive option on connect()",
}


def _scenario_ids(item) -> frozenset:
    """The scenario ids (@C-NNNN) of the scenario ``item`` runs, recorded by
    tests/conftest.py's pytest_bdd_apply_tag (they are not pytest marks)."""
    return getattr(getattr(item, "function", None), SCENARIO_IDS_ATTR, frozenset())


def pytest_collection_modifyitems(items):
    for item in items:
        tags = _scenario_ids(item)
        for tag, reason in PENDING.items():
            if tag in tags:
                item.add_marker(
                    pytest.mark.xfail(strict=True, reason=f"pending ({tag}): {reason}")
                )
