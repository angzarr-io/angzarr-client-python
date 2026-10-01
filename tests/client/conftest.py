"""pytest configuration for the client-surface and parity feature tiers.

Scenarios of the shared spec that name the engine surface a client library
exposes when it hosts components itself (router builders, handler decorators,
handler response types, gRPC server adapters, compensation helpers) do not
apply here: Python components are hosted by the shared router
(``angzarr_client.router``) through wiring generated from this repository's
codegen templates, and the router's own conformance suite covers that
behaviour (tests/router). They are reported as skipped, with this reason.
"""

from __future__ import annotations

import pytest

ENGINE_SURFACE = {
    "C-0090": "Router runtime types: the engine is angzarr_client.router (shared router)",
    "C-0091": "handler kind declarations: components are declared in proto and generated",
    "C-0092": "method markers: handler methods come from the generated <Component>Handler",
    "C-0093": "handler response types: generated wiring returns framework protos",
    "C-0094": "gRPC server adapters: no engine-specific adapters in the router-binding client",
    "C-0102": "compensation helpers: rejection handlers return framework protos via generated wiring",
}

PENDING = {
    "C-0336": "angzarr_client exposes no connect-timeout option on connect()",
    "C-0337": "angzarr_client exposes no keep-alive option on connect()",
}


def pytest_collection_modifyitems(items):
    for item in items:
        for tag, reason in ENGINE_SURFACE.items():
            if item.get_closest_marker(tag) is not None:
                item.add_marker(
                    pytest.mark.skip(reason=f"engine surface ({tag}): {reason}")
                )
        for tag, reason in PENDING.items():
            if item.get_closest_marker(tag) is not None:
                item.add_marker(
                    pytest.mark.xfail(strict=True, reason=f"pending ({tag}): {reason}")
                )
