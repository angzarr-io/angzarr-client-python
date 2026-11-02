"""Generate the package's protobuf modules, and the router tier's fixture
messages, from the angzarr-project submodule.

Used by the wheel build hook (hatch_build.py) and the just recipes, so a
``pip install`` from a git checkout or an sdist produces the same package as
``just build``:

- ``generate_protos``: the framework protos (angzarr-project) as Python
  modules under angzarr_client/proto/, generated with grpcio-tools (protoc
  and the gRPC plugin; no network), imports rerooted under
  ``angzarr_client.proto``: core (``io.angzarr.v1``), the component
  contract (``io.angzarr.component.v1``) and both transports
  (``io.angzarr.transport.{embedded,distributed}.v1``; decision 14.64);
  with them, for each package with reason keys, each key's args and status,
  read from its ``(io.angzarr.v1.reason)`` option (decisions 14.60 and
  14.65), as ``reason_args.py`` beside its
  ``reasons_pb2.py``, and the spec's en-US catalog of its keys,
  ``reasons.en-US.ftl``, copied there (decisions 14.60 and 14.64);
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

# Proto trees generated into the package, relative to their proto root: the
# four packages (decision 14.64) and sererr, which the distributed
# transport's dead-letter envelope imports.
PROJECT_TREES = (
    "io/angzarr/v1",
    "io/angzarr/component",
    "io/angzarr/transport",
    "sererr",
)
# Each package with reason keys, by its directory relative to the proto root
# (and to the package's proto/), with its key enums: its reasons.proto, its
# catalog, and the args module generated from it sit there.
REASON_PACKAGES = {
    "io/angzarr/v1": ("RouterReason", "HostReason", "CoordinatorReason"),
    "io/angzarr/transport/embedded/v1": ("HostReason",),
    "io/angzarr/transport/distributed/v1": ("CoordinatorReason",),
}
# Core's reasons module, which declares the (reason) option.
CORE_REASONS = "io/angzarr/v1"

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
    declared = _declared(out / CORE_REASONS / "reasons_pb2.py")
    for package in REASON_PACKAGES:
        shutil.copyfile(
            PROJECT_PROTO / package / "reasons.en-US.ftl",
            out / package / "reasons.en-US.ftl",
        )
        args, statuses = declared[package]
        (out / package / "reason_args.py").write_text(reason_args(args, statuses))
    return _generated(out)


def _declared(
    core_reasons_pb2: pathlib.Path,
) -> dict[str, tuple[dict[str, tuple[str, ...]], dict[str, int | None]]]:
    """Each package's live reason keys' args and statuses, by package and
    key (:func:`_package_args`), read from
    the descriptors protoc compiles of every REASON_PACKAGES reasons.proto.
    The ``(reason)`` option is read through core's generated module at
    ``core_reasons_pb2``, which imports nothing of the package; the
    transports' generated modules import core's, so they are not loaded."""
    import importlib.util
    import tempfile

    from google.protobuf import descriptor_pb2

    spec = importlib.util.spec_from_file_location(
        "_angzarr_reasons_pb2", core_reasons_pb2
    )
    core = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(core)
    with tempfile.TemporaryDirectory() as scratch:
        set_path = pathlib.Path(scratch) / "reasons.binpb"
        _protoc(
            [
                f"-I{PROJECT_PROTO}",
                f"-I{_grpc_tools_include()}",
                f"--descriptor_set_out={set_path}",
                *(f"{package}/reasons.proto" for package in REASON_PACKAGES),
            ]
        )
        files = descriptor_pb2.FileDescriptorSet.FromString(set_path.read_bytes()).file
    by_name = {f.name: f for f in files}
    return {
        package: _package_args(by_name[f"{package}/reasons.proto"], enums, core.reason)
        for package, enums in REASON_PACKAGES.items()
    }


# ReasonStatus's READINESS (reasons.proto; decision 14.65): no
# google.rpc.Code, since a readiness refusal never travels as a status.
_READINESS = 100


def _package_args(
    file, enums: tuple[str, ...], reason
) -> tuple[dict[str, tuple[str, ...]], dict[str, int | None]]:
    """One reasons.proto's live keys' args and statuses, by key, from its
    descriptor ``file``, for its key ``enums``: a status as the
    google.rpc.Code number it travels with (a ReasonStatus value's number
    is its code's), None for a readiness refusal."""
    from google.protobuf import descriptor_pb2

    found = {e.name: e for e in file.enum_type}
    declared: dict[str, tuple[str, ...]] = {}
    statuses: dict[str, int | None] = {}
    for enum in enums:
        for value in found[enum].value:
            if value.number == 0:
                continue
            # Parsed again now core's module registered the option.
            options = descriptor_pb2.EnumValueOptions.FromString(
                value.options.SerializeToString()
            )
            if not options.HasExtension(reason):
                raise ProtoBuildError(
                    f"{value.name} declares no args (its (reason) option)"
                )
            option = options.Extensions[reason]
            if not option.status:
                raise ProtoBuildError(
                    f"{value.name} declares no status (its (reason) option)"
                )
            declared[value.name] = tuple(option.args)
            statuses[value.name] = (
                None if option.status == _READINESS else option.status
            )
    return declared, statuses


def reason_args(
    declared: dict[str, tuple[str, ...]], statuses: dict[str, int | None]
) -> str:
    """``reason_args.py`` for one package's keys, ``declared`` (each key's
    args, by key) and ``statuses`` (each key's status): ``ARGS``,
    ``STATUSES`` and one constant per arg name, its name in SCREAMING_SNAKE."""
    names = sorted({arg for args in declared.values() for arg in args})
    lines = [
        '"""Each of this package\'s reason keys\' args and status, generated by',
        "scripts/proto_build.py from its reasons.proto's (io.angzarr.v1.reason)",
        'options (decisions 14.60, 14.64 and 14.65). Do not edit."""',
        "",
        "ARGS: dict[str, tuple[str, ...]] = {",
        *(f"    {key!r}: {args!r}," for key, args in declared.items()),
        "}",
        "",
        "# The google.rpc.Code each key travels with; None for a readiness",
        "# refusal, which never travels as a status.",
        "STATUSES: dict[str, int | None] = {",
        *(f"    {key!r}: {status!r}," for key, status in statuses.items()),
        "}",
        "",
        *(f"{name.upper()} = {name!r}" for name in names),
        "",
    ]
    return "\n".join(lines)


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
