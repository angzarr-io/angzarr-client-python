"""Wheel build hook: the generated protos and the router-ffi library are built
into every wheel, which is tagged with the manylinux platform the library
supports (scripts/native_build.py)."""

from __future__ import annotations

import importlib.util
import pathlib

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

_SCRIPT = pathlib.Path(__file__).resolve().parent / "scripts" / "native_build.py"


def _native_build():
    spec = importlib.util.spec_from_file_location("native_build", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class NativeBuildHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict) -> None:
        if self.target_name != "wheel":
            return
        native = _native_build()
        native.generate_protos()
        lib = native.build_router_lib()
        build_data["pure_python"] = False
        build_data["tag"] = f"py3-none-{native.wheel_platform_tag(lib)}"
