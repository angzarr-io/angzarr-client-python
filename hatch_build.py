"""Wheel build hook: the generated protos are built into every wheel
(scripts/proto_build.py). The wheel is pure Python."""

from __future__ import annotations

import importlib.util
import pathlib

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

_SCRIPT = pathlib.Path(__file__).resolve().parent / "scripts" / "proto_build.py"


def _proto_build():
    spec = importlib.util.spec_from_file_location("proto_build", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProtoBuildHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict) -> None:
        if self.target_name != "wheel":
            return
        _proto_build().generate_protos()
