"""A child process for tests/client/steps/test_child.py
(parity/client/child.feature): an "order" aggregate component under
:func:`angzarr_client.child.run_child`.

Run as ``python -m tests.client.child_order`` from the repository root.

With ``ANGZARR_TEST_READINESS_GATE`` naming a directory, the child's
readiness is held, so a test forces the interleaving C-0750 is about
instead of waiting for it: the host's readiness supervisor, which turns its
health SERVING, does not start until the file ``release`` appears in that
directory, and the first time the child's shim finds its health not yet
SERVING, the fixture writes the file ``held`` there. A test that sees
``held`` knows the child is bound, has looked at its health, and found it
NOT_SERVING.
"""

from __future__ import annotations

import asyncio
import os
import pathlib

from google.protobuf import wrappers_pb2
from grpc_health.v1 import health_pb2

from angzarr_client import ComponentHost, configure_logging, readiness
from angzarr_client.child import run_child
from angzarr_client.proto.io.angzarr.v1 import types_pb2
from angzarr_client.router import AggregateDispatch, Rebuilder, Router, pack

GATE_ENV = "ANGZARR_TEST_READINESS_GATE"
RULES_VERSION = "0.0.0+test"


def _order() -> AggregateDispatch:
    dispatch = AggregateDispatch(
        name="order-component",
        domain="order",
        rebuilder=Rebuilder(wrappers_pb2.Int64Value),
    )

    def handle(command, state, cctx):
        book = types_pb2.EventBook()
        book.pages.add().payload.CopyFrom(pack(wrappers_pb2.Int64Value(value=1)))
        return book

    dispatch.on_command("google.protobuf.StringValue", handle)
    return dispatch


def _hold_readiness(host: ComponentHost, gate: pathlib.Path) -> None:
    """Hold ``host``'s readiness until ``gate/release`` exists, and write
    ``gate/held`` the first time its health is read while not SERVING."""
    supervise = readiness.run_supervisor

    async def held_supervisor(**kwargs):
        while not (gate / "release").exists():
            await asyncio.sleep(0.01)
        await supervise(**kwargs)

    readiness.run_supervisor = held_supervisor
    health_status = host.health_status

    def status(service: str = "") -> int | None:
        found = health_status(service)
        if found != health_pb2.HealthCheckResponse.SERVING:
            (gate / "held").touch()
        return found

    host.health_status = status


def main() -> None:
    configure_logging()
    host = ComponentHost(Router(), rules_version=RULES_VERSION)
    dispatch = _order()
    host.add_aggregate(
        dispatch,
        types_pb2.ComponentDescriptor(
            name=dispatch.name, kind=dispatch.kind, rules_version=RULES_VERSION
        ),
    )
    gate = os.environ.get(GATE_ENV)
    if gate:
        _hold_readiness(host, pathlib.Path(gate))
    run_child(host)


if __name__ == "__main__":
    main()
