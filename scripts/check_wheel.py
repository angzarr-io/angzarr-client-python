#!/usr/bin/env python3
"""Prove an installed angzarr-client works without a source checkout: the
framework protos import, the router accepts a component, and nothing is
imported from the source tree."""

from __future__ import annotations

import pathlib
import sys


def main() -> int:
    here = pathlib.Path(__file__).resolve().parents[1]
    sys.path = [p for p in sys.path if pathlib.Path(p or ".").resolve() != here]

    import angzarr_client
    from angzarr_client.proto.io.angzarr.v1 import types_pb2
    from angzarr_client.router import AggregateDispatch, Rebuilder, Router

    package = pathlib.Path(angzarr_client.__file__).resolve().parent
    if here in package.parents:
        print(f"FAIL: angzarr_client imported from the source tree ({package})")
        return 1
    router = Router()
    try:
        router.register_aggregate(
            AggregateDispatch("WheelCheck", "wheel-check", Rebuilder(types_pb2.Cover))
        )
    finally:
        router.close()
    print(f"ok: angzarr-client {angzarr_client.__version__} from {package}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
