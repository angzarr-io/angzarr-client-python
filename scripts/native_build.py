"""Build the generated and native parts of the package from the submodules.

Used by the wheel build hook (hatch_build.py) and the just recipes, so a
``pip install`` from a git checkout or an sdist produces the same package as
``just build``:

- ``generate_protos`` — the framework protos (angzarr-project) and the router
  ABI protos (angzarr-router) as Python modules under angzarr_client/proto/,
  generated with grpcio-tools (protoc + the gRPC plugin; no network) and with
  imports rerooted under ``angzarr_client.proto``;
- ``build_router_lib`` — the router-ffi cdylib from the angzarr-router
  submodule (cargo; grpcio-tools' protoc serves prost-build), copied into
  angzarr_client/router/_lib/;
- ``wheel_platform_tag`` — the manylinux tag the built library supports,
  read from the ELF itself: the highest GLIBC symbol version it needs, and a
  refusal of any shared-library dependency outside the manylinux set.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import stat
import struct
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROTO_OUT = ROOT / "angzarr_client" / "proto"
LIB_DIR = ROOT / "angzarr_client" / "router" / "_lib"
LIB_NAME = "libangzarr_router_ffi.so"
PROJECT_PROTO = ROOT / "angzarr-project" / "proto"
ROUTER = ROOT / "angzarr-router"
ROUTER_PROTO = ROUTER / "proto"

# Proto trees generated into the package, relative to their proto root.
PROJECT_TREES = ("io/angzarr/v1", "io/angzarr/status", "sererr")
ROUTER_TREES = ("io/angzarr/router",)

# The shared libraries a manylinux wheel may depend on (PEP 600 / auditwheel).
MANYLINUX_LIBS = frozenset(
    {
        "libc.so.6",
        "libm.so.6",
        "libdl.so.2",
        "librt.so.1",
        "libpthread.so.0",
        "libgcc_s.so.1",
        "ld-linux-x86-64.so.2",
    }
)
# PEP 600's floor: manylinux2014 is glibc 2.17.
MIN_GLIBC_MINOR = 17


class NativeBuildError(RuntimeError):
    """The native part of the package cannot be built here."""


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
        raise NativeBuildError(f"protoc failed ({code}): {' '.join(args)}")


def generate_protos(out: pathlib.Path = PROTO_OUT) -> list[pathlib.Path]:
    """Generate the package's protobuf and gRPC modules; returns the files
    written."""
    for required in (PROJECT_PROTO, ROUTER_PROTO):
        if not required.is_dir():
            raise NativeBuildError(
                f"{required} is missing: initialise the submodules "
                "(git submodule update --init angzarr-project angzarr-router)"
            )
    for stale in ("io", "sererr"):
        shutil.rmtree(out / stale, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)
    include = [
        f"-I{PROJECT_PROTO}",
        f"-I{ROUTER_PROTO}",
        f"-I{_grpc_tools_include()}",
    ]
    outputs = [f"--python_out={out}", f"--pyi_out={out}", f"--grpc_python_out={out}"]
    project = _protos(PROJECT_PROTO, PROJECT_TREES)
    router = _protos(ROUTER_PROTO, ROUTER_TREES)
    _protoc([*include, *outputs, *project])
    _protoc([*include, *outputs, *router])

    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import fixup_gen_imports
    finally:
        sys.path.pop(0)
    fixup_gen_imports.fixup(
        out,
        {
            "io": "angzarr_client.proto.io",
            "sererr": "angzarr_client.proto.sererr",
        },
    )
    return sorted(
        p for p in out.rglob("*") if p.is_file() and "__pycache__" not in p.parts
    )


def _protoc_wrapper(directory: pathlib.Path) -> pathlib.Path:
    """An executable ``protoc`` that runs grpcio-tools' protoc in this
    interpreter (prost-build runs ``$PROTOC``)."""
    wrapper = directory / "protoc"
    wrapper.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" -m grpc_tools.protoc "$@"\n'
    )
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return wrapper


def build_router_lib(target_dir: pathlib.Path | None = None) -> pathlib.Path:
    """Build the router-ffi cdylib (release) and copy it into the package;
    returns the copied library."""
    if sys.platform != "linux":
        raise NativeBuildError(
            f"angzarr-client builds on Linux only (this is {sys.platform})"
        )
    cargo = shutil.which("cargo")
    if cargo is None:
        raise NativeBuildError(
            "cargo is required to build the router-ffi library (https://rustup.rs)"
        )
    if not (ROUTER / "Cargo.toml").is_file():
        raise NativeBuildError(
            f"{ROUTER} is missing: initialise the submodule (git submodule update --init angzarr-router)"
        )
    target = target_dir or ROOT / ".router-target"
    with tempfile.TemporaryDirectory() as tools:
        env = dict(os.environ)
        env["ANGZARR_PROJECT_PROTO"] = str(PROJECT_PROTO)
        env.setdefault("PROTOC", str(_protoc_wrapper(pathlib.Path(tools))))
        env.setdefault("PROTOC_INCLUDE", str(_grpc_tools_include()))
        subprocess.run(
            [
                cargo,
                "build",
                "--manifest-path",
                str(ROUTER / "Cargo.toml"),
                "-p",
                "angzarr-router-ffi",
                "--release",
                "--target-dir",
                str(target),
            ],
            check=True,
            env=env,
        )
    built = target / "release" / LIB_NAME
    if not built.is_file():
        raise NativeBuildError(f"cargo produced no {built}")
    LIB_DIR.mkdir(parents=True, exist_ok=True)
    dest = LIB_DIR / LIB_NAME
    shutil.copy2(built, dest)
    return dest


# --- ELF inspection (64-bit little-endian) ---------------------------------

_SHT_DYNAMIC = 6
_SHT_GNU_VERNEED = 0x6FFFFFFE
_DT_NEEDED = 1
_EM_X86_64 = 62


def _sections(data: bytes) -> list[tuple[int, int, int, int]]:
    """(type, offset, size, link) of every section header."""
    if data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
        raise NativeBuildError("not a 64-bit little-endian ELF file")
    shoff = struct.unpack_from("<Q", data, 0x28)[0]
    shentsize, shnum = struct.unpack_from("<HH", data, 0x3A)
    out = []
    for i in range(shnum):
        base = shoff + i * shentsize
        sh_type = struct.unpack_from("<I", data, base + 4)[0]
        sh_offset, sh_size = struct.unpack_from("<QQ", data, base + 0x18)
        sh_link = struct.unpack_from("<I", data, base + 0x28)[0]
        out.append((sh_type, sh_offset, sh_size, sh_link))
    return out


def _cstr(data: bytes, offset: int) -> str:
    end = data.index(b"\0", offset)
    return data[offset:end].decode()


def elf_requirements(path: pathlib.Path) -> tuple[int, set[str], set[str]]:
    """(e_machine, needed shared libraries, versioned symbol requirements
    such as ``GLIBC_2.34``) of an ELF shared object."""
    data = path.read_bytes()
    sections = _sections(data)
    machine = struct.unpack_from("<H", data, 0x12)[0]
    needed: set[str] = set()
    versions: set[str] = set()
    for sh_type, offset, size, link in sections:
        strtab = sections[link][1]
        if sh_type == _SHT_DYNAMIC:
            for entry in range(offset, offset + size, 16):
                tag, val = struct.unpack_from("<qQ", data, entry)
                if tag == _DT_NEEDED:
                    needed.add(_cstr(data, strtab + val))
        elif sh_type == _SHT_GNU_VERNEED:
            vn = offset
            while True:
                _ver, cnt, _file, aux, nxt = struct.unpack_from("<HHIII", data, vn)
                va = vn + aux
                for _ in range(cnt):
                    _hash, _flags, _other, name, vna_next = struct.unpack_from(
                        "<IHHII", data, va
                    )
                    versions.add(_cstr(data, strtab + name))
                    va += vna_next
                if nxt == 0:
                    break
                vn += nxt
    return machine, needed, versions


def wheel_platform_tag(lib: pathlib.Path) -> str:
    """The ``manylinux_2_<N>_x86_64`` tag ``lib`` satisfies."""
    machine, needed, versions = elf_requirements(lib)
    if machine != _EM_X86_64:
        raise NativeBuildError(f"{lib.name} is not x86_64 (e_machine {machine})")
    outside = sorted(needed - MANYLINUX_LIBS)
    if outside:
        raise NativeBuildError(
            f"{lib.name} links libraries outside manylinux: {outside}"
        )
    minors = [
        int(v.split(".")[1])
        for v in versions
        if v.startswith("GLIBC_2.") and v.split(".")[1].isdigit()
    ]
    minor = max([MIN_GLIBC_MINOR, *minors])
    return f"manylinux_2_{minor}_x86_64"


def main(argv: list[str]) -> int:
    commands = {"proto": generate_protos, "router-lib": build_router_lib}
    if len(argv) != 2 or argv[1] not in (*commands, "tag"):
        print("usage: native_build.py proto | router-lib | tag", file=sys.stderr)
        return 2
    if argv[1] == "tag":
        print(wheel_platform_tag(LIB_DIR / LIB_NAME))
        return 0
    result = commands[argv[1]]()
    print(
        result
        if isinstance(result, pathlib.Path)
        else f"{len(result)} files under {PROTO_OUT}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
