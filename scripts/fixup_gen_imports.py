#!/usr/bin/env python3
"""Reroot generated protobuf imports under the packages they are imported as.

The protocolbuffers/python and grpc/python plugins emit cross-file imports by
proto PATH, so a file under io/angzarr/v1/ imports a sibling as
``from io.angzarr.v1 import types_pb2``. Those names cannot be imported as
written: ``io`` is the stdlib module (loaded, and not a package, before any
user code runs), and ``sererr`` would be a stray top-level package. This
script rewrites each import whose module starts with a mapped prefix (longest
prefix first) to the package that module actually lives in.

``google.*`` imports are never mapped: google.protobuf is the protobuf runtime
and google.rpc / google.api come from googleapis-common-protos.

    fixup_gen_imports.py <gen dir> <from>=<to> [<from>=<to> ...]

e.g. ``fixup_gen_imports.py angzarr_client/proto io=angzarr_client.proto.io``
rewrites ``from io.angzarr.v1 import x`` to
``from angzarr_client.proto.io.angzarr.v1 import x``.
"""

from __future__ import annotations

import pathlib
import re
import sys

GLOBS = ("*_pb2.py", "*_pb2.pyi", "*_pb2_grpc.py")


def fixup(gen_dir: pathlib.Path, mapping: dict[str, str]) -> int:
    """Rewrite mapped imports in every generated file under ``gen_dir``;
    returns the number of files changed."""
    prefixes = sorted(mapping, key=len, reverse=True)
    pattern = re.compile(
        r"^(\s*)(from|import) (" + "|".join(map(re.escape, prefixes)) + r")(?=[.\s])",
        re.MULTILINE,
    )
    rewritten = 0
    for glob in GLOBS:
        for path in sorted(gen_dir.rglob(glob)):
            text = path.read_text()
            new = pattern.sub(
                lambda m: f"{m.group(1)}{m.group(2)} {mapping[m.group(3)]}", text
            )
            if new != text:
                path.write_text(new)
                rewritten += 1
    return rewritten


def main(argv: list[str]) -> int:
    if len(argv) < 3 or not all("=" in a for a in argv[2:]):
        print(__doc__, file=sys.stderr)
        return 2
    gen_dir = pathlib.Path(argv[1])
    if not gen_dir.is_dir():
        print(f"{gen_dir}: not a directory", file=sys.stderr)
        return 1
    mapping = dict(a.split("=", 1) for a in argv[2:])
    count = fixup(gen_dir, mapping)
    print(f"rerooted imports in {count} file(s) under {gen_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
