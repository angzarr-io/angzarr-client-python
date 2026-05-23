"""Step defs for features/client/parity.feature.

Verifies the canonical cross-language public surface is exposed from
angzarr_client's public root or a canonical subpackage. Each scenario names
the symbols it requires; this file resolves them by walking a short list
of canonical import paths and asserting presence.
"""

from __future__ import annotations

import importlib
from typing import Any

from pytest_bdd import given, parsers, scenarios, then

scenarios("../../parity/client/parity.feature")


_SEARCH_PATHS = (
    "angzarr_client",
    "angzarr_client.retry",
    "angzarr_client.testing",
    "angzarr_client.testing.builders",
    "angzarr_client.testing.uuid",
    "angzarr_client.testing.context",
)

_ERROR_CLASSES = (
    "ClientError",
    "CommandRejectedError",
    "ConnectionError",
    "TransportError",
    "GRPCError",
    "InvalidArgumentError",
    "InvalidTimestampError",
)


def _resolve(name: str) -> tuple[str | None, Any]:
    for mod_name in _SEARCH_PATHS:
        try:
            mod = importlib.import_module(mod_name)
        except ImportError:
            continue
        if hasattr(mod, name):
            return mod_name, getattr(mod, name)
    return None, None


def _assert_exported(name: str) -> None:
    mod_name, obj = _resolve(name)
    assert obj is not None, (
        f'"{name}" was not found in angzarr_client or any canonical subpackage '
        f"(searched: {', '.join(_SEARCH_PATHS)})"
    )


@given("the angzarr client library is importable at its public root")
def _given_importable() -> None:
    importlib.import_module("angzarr_client")


@then(parsers.parse('the "{name}" symbol is exported'))
def _then_symbol_exported(name: str) -> None:
    _assert_exported(name)


@then(parsers.parse('the "{name}" kind declaration is exported'))
def _then_kind_decl_exported(name: str) -> None:
    _assert_exported(name)


@then(parsers.parse('the "{name}" method marker is exported'))
def _then_method_marker_exported(name: str) -> None:
    _assert_exported(name)


@then(parsers.parse('the "{name}" constant is exported'))
def _then_constant_exported(name: str) -> None:
    _assert_exported(name)


@then(parsers.parse('the client exposes the "{predicate}" error predicate'))
def _then_predicate_exposed(predicate: str) -> None:
    angzarr_client = importlib.import_module("angzarr_client")
    missing: list[str] = []
    for cls_name in _ERROR_CLASSES:
        cls = getattr(angzarr_client, cls_name, None)
        if cls is None:
            missing.append(f"{cls_name} not exported")
            continue
        if not hasattr(cls, predicate):
            missing.append(f"{cls_name}.{predicate} missing")
    assert not missing, f'Predicate "{predicate}" missing on: {missing}'
