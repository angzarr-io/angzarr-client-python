"""A child process for tests/router/test_hand_child.py: one component
written by hand (a dispatch table from tests/router/hand.py and
a descriptor from :func:`angzarr_client.describe`, no generated code or hex)
under :func:`angzarr_client.child.run_child`.

Run as ``python -m tests.router.child_hand <kind>`` from the repository root,
``<kind>`` one of aggregate, policy, process_manager, projector.
"""

from __future__ import annotations

import sys

from angzarr_client import ComponentHost, configure_logging, describe
from angzarr_client.child import run_child

from .contract import TEST_RULES_VERSION, counter_aggregate
from .gen import order_pb2
from .hand import fulfilment, reserving, totals


def host(kind: str) -> ComponentHost:
    """The host of the hand-written component of ``kind``."""
    starting = {"process_manager": [order_pb2.StartFulfillment]}
    table = {
        "aggregate": lambda: counter_aggregate([]),
        "policy": reserving,
        "process_manager": fulfilment,
        "projector": totals,
    }[kind]()
    descriptor = describe(
        table, rules_version=TEST_RULES_VERSION, starting_types=starting.get(kind, ())
    )
    host = ComponentHost(rules_version=TEST_RULES_VERSION)
    return getattr(host, f"add_{kind}")(table, descriptor)


def main() -> None:
    configure_logging()
    run_child(host(sys.argv[1]))


if __name__ == "__main__":
    main()
