"""Generate the package's protobuf modules, and the router tier's fixture
messages, from the angzarr-project submodule.

Used by the wheel build hook (hatch_build.py) and the just recipes, so a
``pip install`` from a git checkout or an sdist produces the same package as
``just build``:

- ``generate_protos``: the framework protos (angzarr-project) as Python
  modules under angzarr_client/proto/, generated with grpcio-tools (protoc
  and the gRPC plugin; no network), imports rerooted under
  ``angzarr_client.proto``;
- ``generate_fixtures``: the handler-contract tier's fixture messages
  (angzarr-project features/handler-contract/fixture/*.proto) under
  tests/router/gen/, the messages the tier's steps build their components
  over (``just contract-gen``).
"""

from __future__ import annotations

import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROTO_OUT = ROOT / "angzarr_client" / "proto"
PROJECT_PROTO = ROOT / "angzarr-project" / "proto"
FIXTURE_PROTO = ROOT / "angzarr-project" / "features" / "handler-contract" / "fixture"
FIXTURE_OUT = ROOT / "tests" / "router" / "gen"

# Proto trees generated into the package, relative to their proto root.
PROJECT_TREES = ("io/angzarr/v1", "io/angzarr/status", "sererr")
# Where the generated framework modules live, by proto package root.
FRAMEWORK = {"io": "angzarr_client.proto.io", "sererr": "angzarr_client.proto.sererr"}


class ProtoBuildError(RuntimeError):
    """The generated part of the package cannot be built here."""


def _grpc_tools_include() -> pathlib.Path:
    import grpc_tools

    return pathlib.Path(grpc_tools.__file__).resolve().parent / "_proto"


def _protos(root: pathlib.Path, trees: tuple[str, ...]) -> list[str]:
    files: list[str] = []
    for tree in trees:
        files += sorted(
            p.relative_to(root).as_posix() for p in (root / tree).rglob("*.proto")
        )
    return files


def _protoc(args: list[str]) -> None:
    from grpc_tools import protoc

    code = protoc.main(["grpc_tools.protoc", *args])
    if code != 0:
        raise ProtoBuildError(f"protoc failed ({code}): {' '.join(args)}")


def _fixup(out: pathlib.Path) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import fixup_gen_imports
    finally:
        sys.path.pop(0)
    fixup_gen_imports.fixup(out, FRAMEWORK)


def _require(path: pathlib.Path) -> None:
    if not path.is_dir():
        raise ProtoBuildError(
            f"{path} is missing: initialise the submodule "
            "(git submodule update --init angzarr-project)"
        )


def _generated(out: pathlib.Path) -> list[pathlib.Path]:
    return sorted(
        p for p in out.rglob("*") if p.is_file() and "__pycache__" not in p.parts
    )


def generate_protos(out: pathlib.Path = PROTO_OUT) -> list[pathlib.Path]:
    """Generate the package's protobuf and gRPC modules; returns the files
    written."""
    _require(PROJECT_PROTO)
    for stale in ("io", "sererr"):
        shutil.rmtree(out / stale, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)
    _protoc(
        [
            f"-I{PROJECT_PROTO}",
            f"-I{_grpc_tools_include()}",
            f"--python_out={out}",
            f"--pyi_out={out}",
            f"--grpc_python_out={out}",
            *_protos(PROJECT_PROTO, PROJECT_TREES),
        ]
    )
    _fixup(out)
    return _generated(out)


def generate_fixtures(out: pathlib.Path = FIXTURE_OUT) -> list[pathlib.Path]:
    """Generate the handler-contract tier's fixture messages into ``out``
    (replaced whole); returns the files written."""
    _require(FIXTURE_PROTO)
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    _protoc(
        [
            f"-I{FIXTURE_PROTO}",
            f"-I{PROJECT_PROTO}",
            f"-I{_grpc_tools_include()}",
            f"--python_out={out}",
            *sorted(p.name for p in FIXTURE_PROTO.glob("*.proto")),
        ]
    )
    _fixup(out)
    return _generated(out)


def main(argv: list[str]) -> int:
    commands = {
        "proto": (generate_protos, PROTO_OUT),
        "fixtures": (generate_fixtures, FIXTURE_OUT),
    }
    if len(argv) != 2 or argv[1] not in commands:
        print("usage: proto_build.py proto | fixtures", file=sys.stderr)
        return 2
    generate, out = commands[argv[1]]
    print(f"{len(generate())} files under {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
