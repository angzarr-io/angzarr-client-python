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
    # Decision 14.37 on the client side: a ClientError's text is its code
    # then its args, and message() renders from a catalog by the code. The
    # client still carries English; these scenarios came in with the
    # angzarr-project pin to 74690c7.
    **dict.fromkeys(
        (
            "C-0131",
            "C-0341",
            "C-0344",
            "C-0345",
            "C-0349",
            "C-0352",
            "C-0355",
            "C-0363",
        ),
        "ClientError still carries English, not its code and args (14.37)",
    ),
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
