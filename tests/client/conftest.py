"""pytest configuration for the client-surface and parity feature tiers.

Scenarios the client cannot satisfy yet are strict xfail with the reason, so
one that starts passing fails the run until it is taken off this list.
"""

from __future__ import annotations

import pytest

PENDING = {
    "C-0336": "angzarr_client exposes no connect-timeout option on connect()",
    "C-0337": "angzarr_client exposes no keep-alive option on connect()",
    "C-0197": "no delivery-mode choice for senders (HandleAsync); pending item-4 "
    "sender API sign-off",
    "C-0198": "no delivery-mode choice for senders (HandleSync); pending item-4 "
    "sender API sign-off",
    "C-0307": "no expected-sequence setter on CommandBuilder (Handling); pending "
    "item-4 sender API sign-off",
    "C-0308": "no expected-sequence setter on CommandBuilder (Handling); pending "
    "item-4 sender API sign-off",
    "C-0311": "the chain includes with_sequence (Handling.expected_sequence); "
    "pending item-4 sender API sign-off",
    "C-0315": "MergeStrategy is gone; its successor is Handling.conflict_resolution, "
    "pending item-4 sender API sign-off",
    "C-0316": "MergeStrategy is gone; its successor is Handling.conflict_resolution, "
    "pending item-4 sender API sign-off",
    "C-0371": "EventPage has no PayloadReference arm in the pinned types.proto "
    "(offloaded payloads left the wire); the scenario predates it",
    "C-0118": "covers carry no correlation_id in the pinned types.proto "
    "(correlation is recorded per page); the scenario predates it",
}


def pytest_collection_modifyitems(items):
    for item in items:
        for tag, reason in PENDING.items():
            if item.get_closest_marker(tag) is not None:
                item.add_marker(
                    pytest.mark.xfail(strict=True, reason=f"pending ({tag}): {reason}")
                )
