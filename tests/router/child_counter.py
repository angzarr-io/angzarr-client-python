"""A child process for tests/router/test_child.py: the handler-contract
tier's CounterAggregate under :func:`angzarr_client.child.run_child`.

Run as ``python -m tests.router.child_counter`` from the repository root.
"""

from __future__ import annotations

from angzarr_client import ComponentHost, configure_logging
from angzarr_client.child import run_child
from angzarr_client.router import Router

from .builders import TEST_RULES_VERSION
from .contract import counter_aggregate, described


def main() -> None:
    configure_logging()
    host = ComponentHost(Router(), rules_version=TEST_RULES_VERSION)
    host.add_aggregate(*described(counter_aggregate([])))
    run_child(host)


if __name__ == "__main__":
    main()
