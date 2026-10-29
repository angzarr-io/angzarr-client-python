"""A child process for tests/router/test_child.py: the conformance fixture's
CounterAggregate under :func:`angzarr_client.child.run_child`.

Run as ``python -m tests.router.child_counter`` from the repository root.
"""

from __future__ import annotations

from angzarr_client import ComponentHost, configure_logging
from angzarr_client.child import run_child
from angzarr_client.router import Router

from .builders import TEST_RULES_VERSION
from .fixture import CounterAggregate
from .gen.test.counter import counter_aggregate_angzarr


def main() -> None:
    configure_logging()
    host = ComponentHost(Router(), rules_version=TEST_RULES_VERSION)
    host.add_aggregate(
        counter_aggregate_angzarr.new_counter_aggregate_dispatch(CounterAggregate())
    )
    run_child(host)


if __name__ == "__main__":
    main()
