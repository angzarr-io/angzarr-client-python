"""pytest configuration for the client-surface and parity feature tiers.

Scenarios the client cannot satisfy yet are strict xfail with the reason, so
one that starts passing fails the run until it is taken off this list.
"""

from __future__ import annotations

import pytest

PENDING = {
    "C-0336": "angzarr_client exposes no connect-timeout option on connect()",
    "C-0337": "angzarr_client exposes no keep-alive option on connect()",
}


def pytest_collection_modifyitems(items):
    for item in items:
        for tag, reason in PENDING.items():
            if item.get_closest_marker(tag) is not None:
                item.add_marker(
                    pytest.mark.xfail(strict=True, reason=f"pending ({tag}): {reason}")
                )
