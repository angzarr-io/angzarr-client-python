"""Unit tests for scripts/native_build.py: the manylinux tag read from the
router library's ELF, and the build preconditions."""

from __future__ import annotations

import importlib.util
import pathlib
import re
import shutil
import subprocess

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "native_build.py"
_spec = importlib.util.spec_from_file_location("native_build", _SCRIPT)
native_build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(native_build)

LIB = native_build.LIB_DIR / native_build.LIB_NAME
needs_lib = pytest.mark.skipif(
    not LIB.is_file(), reason="router library not built (just router-lib)"
)


@needs_lib
def test_tag_is_the_highest_glibc_version_the_library_needs():
    tag = native_build.wheel_platform_tag(LIB)
    assert re.fullmatch(r"manylinux_2_\d+_x86_64", tag)
    if shutil.which("readelf"):
        out = subprocess.run(
            ["readelf", "-V", str(LIB)], capture_output=True, text=True
        ).stdout
        minors = {int(m) for m in re.findall(r"GLIBC_2\.(\d+)", out)}
        assert tag == f"manylinux_2_{max(minors | {17})}_x86_64"


@needs_lib
def test_requirements_list_needed_libraries_and_symbol_versions():
    machine, needed, versions = native_build.elf_requirements(LIB)
    assert machine == 62
    assert "libc.so.6" in needed
    assert needed <= native_build.MANYLINUX_LIBS
    assert any(v.startswith("GLIBC_2.") for v in versions)


def test_old_glibc_requirements_floor_at_manylinux2014(monkeypatch, tmp_path):
    monkeypatch.setattr(
        native_build,
        "elf_requirements",
        lambda p: (62, {"libc.so.6"}, {"GLIBC_2.2.5", "GLIBC_2.14"}),
    )
    assert native_build.wheel_platform_tag(tmp_path / "x.so") == "manylinux_2_17_x86_64"


def test_newer_glibc_requirement_raises_the_tag(monkeypatch, tmp_path):
    monkeypatch.setattr(
        native_build,
        "elf_requirements",
        lambda p: (
            62,
            {"libc.so.6"},
            {"GLIBC_2.17", "GLIBC_2.39", "GCC_3.0", "GLIBC_PRIVATE"},
        ),
    )
    assert native_build.wheel_platform_tag(tmp_path / "x.so") == "manylinux_2_39_x86_64"


def test_a_library_outside_manylinux_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(
        native_build,
        "elf_requirements",
        lambda p: (62, {"libc.so.6", "libssl.so.3"}, set()),
    )
    with pytest.raises(native_build.NativeBuildError, match="libssl.so.3"):
        native_build.wheel_platform_tag(tmp_path / "x.so")


def test_a_non_x86_64_library_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(
        native_build, "elf_requirements", lambda p: (183, {"libc.so.6"}, set())
    )
    with pytest.raises(native_build.NativeBuildError, match="not x86_64"):
        native_build.wheel_platform_tag(tmp_path / "x.so")


def test_a_non_elf_file_is_refused(tmp_path):
    bogus = tmp_path / "x.so"
    bogus.write_bytes(b"not an elf at all, just bytes")
    with pytest.raises(native_build.NativeBuildError, match="ELF"):
        native_build.elf_requirements(bogus)


@needs_lib
def test_a_32_bit_elf_header_is_refused(tmp_path):
    data = bytearray(LIB.read_bytes())
    data[4] = 1  # ELFCLASS32
    copy = tmp_path / "x.so"
    copy.write_bytes(bytes(data))
    with pytest.raises(native_build.NativeBuildError, match="64-bit"):
        native_build.elf_requirements(copy)


def test_proto_generation_needs_the_submodules(monkeypatch, tmp_path):
    monkeypatch.setattr(native_build, "PROJECT_PROTO", tmp_path / "missing")
    with pytest.raises(native_build.NativeBuildError, match="submodule"):
        native_build.generate_protos(tmp_path / "out")


def test_router_build_is_linux_only(monkeypatch):
    monkeypatch.setattr(native_build.sys, "platform", "darwin")
    with pytest.raises(native_build.NativeBuildError, match="Linux only"):
        native_build.build_router_lib()


def test_router_build_needs_cargo(monkeypatch):
    monkeypatch.setattr(native_build.sys, "platform", "linux")
    monkeypatch.setattr(native_build.shutil, "which", lambda name: None)
    with pytest.raises(native_build.NativeBuildError, match="cargo is required"):
        native_build.build_router_lib()


def test_router_build_needs_the_router_submodule(monkeypatch, tmp_path):
    monkeypatch.setattr(native_build.sys, "platform", "linux")
    monkeypatch.setattr(native_build.shutil, "which", lambda name: "/usr/bin/cargo")
    monkeypatch.setattr(native_build, "ROUTER", tmp_path)
    with pytest.raises(native_build.NativeBuildError, match="git submodule update"):
        native_build.build_router_lib()


def test_generated_protos_import_under_the_package(tmp_path):
    files = native_build.generate_protos(tmp_path / "proto")
    names = {f.relative_to(tmp_path / "proto").as_posix() for f in files}
    assert {
        "io/angzarr/v1/types_pb2.py",
        "io/angzarr/v1/types_pb2.pyi",
        "io/angzarr/v1/command_handler_pb2_grpc.py",
        "io/angzarr/router/ffi/v1/abi_pb2.py",
        "sererr/v1/sererr_pb2.py",
    } <= names
    text = (tmp_path / "proto" / "io/angzarr/v1/command_handler_pb2.py").read_text()
    assert "from angzarr_client.proto.io.angzarr.v1 import types_pb2" in text
    assert re.search(r"^from io\.", text, re.M) is None


def test_main_reports_usage(capsys):
    assert native_build.main(["native_build.py"]) == 2
    assert native_build.main(["native_build.py", "nope"]) == 2
    assert "usage" in capsys.readouterr().err
