#!/usr/bin/env python3
"""Generate Python protobuf code from buf registry.

This script uses buf to generate Python protobuf and gRPC code from the
buf.build/angzarr/angzarr module.

Usage:
    python scripts/generate_protos.py
    # Or via uv:
    uv run scripts/generate_protos.py
"""

import shutil
import subprocess
import sys
from pathlib import Path


def main() -> int:
    """Generate proto code using buf."""
    repo_root = Path(__file__).parent.parent

    # Run buf generate
    print("Generating Python proto code from buf.build/angzarr/angzarr...")
    result = subprocess.run(
        ["buf", "generate"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print(f"Error generating protos:\n{result.stderr}", file=sys.stderr)
        return 1

    print(result.stdout)

    proto_dir = repo_root / "angzarr_client" / "proto"

    # Relocate top-level packages that buf emits at repo root (e.g. google/)
    # into the angzarr_client/proto/ namespace so downstream imports work.
    for pkg in ("google", "health"):
        src = repo_root / pkg
        if src.exists():
            dst = proto_dir / pkg
            if dst.exists():
                shutil.rmtree(dst)
            shutil.move(str(src), str(dst))
            print(f"Relocated {pkg}/ → angzarr_client/proto/{pkg}/")

    print(f"Fixing imports in {proto_dir}...")

    for py_file in proto_dir.rglob("*.py"):
        content = py_file.read_text()
        # Fix import paths for package structure
        content = content.replace(
            "from angzarr import", "from angzarr_client.proto.angzarr import"
        )
        content = content.replace(
            "from examples import", "from angzarr_client.proto.examples import"
        )
        content = content.replace(
            "from google.api import", "from angzarr_client.proto.google.api import"
        )
        content = content.replace(
            "from health.v1 import", "from angzarr_client.proto.health.v1 import"
        )
        py_file.write_text(content)

    print("Done!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
