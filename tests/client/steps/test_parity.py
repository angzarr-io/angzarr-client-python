"""Step defs for parity/client/parity.feature.

Verifies the canonical cross-language public surface is exposed from
angzarr_client's public root (and the testing helpers from
angzarr_client.testing only).
"""

from __future__ import annotations

import importlib
from typing import Any

from pytest_bdd import given, parsers, scenarios, then

scenarios("parity/client/parity.feature")


_SEARCH_PATHS = ("angzarr_client",)

_TESTING_MODULE = "angzarr_client.testing"

# Every name the testing scenario requires from the testing module.
_TESTING_HELPERS: set[str] = set()

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


@then(parsers.parse('the "{name}" constant is exported with value "{value}"'))
def _then_constant_value(name: str, value: str) -> None:
    _assert_exported(name)
    assert _resolve(name)[1] == value


@then(parsers.parse('the "{name}" symbol is exported from the testing module'))
@then(parsers.parse('the "{name}" constant is exported from the testing module'))
def _then_testing_export(name: str) -> None:
    testing = importlib.import_module(_TESTING_MODULE)
    assert hasattr(testing, name), f'"{name}" is not exported from {_TESTING_MODULE}'
    _TESTING_HELPERS.add(name)


@then("none of the testing helpers is exported from the client's root")
def _then_testing_gated() -> None:
    root = importlib.import_module("angzarr_client")
    assert _TESTING_HELPERS, "no testing helpers were named before this step"
    leaked = sorted(n for n in _TESTING_HELPERS if hasattr(root, n))
    assert not leaked, f"testing helpers exported from angzarr_client: {leaked}"


# Root names a dispatch engine of its own would export (decorators, Router
# builders, handler gRPC adapters, compensation helpers).
_ENGINE_NAMES = (
    "Router",
    "CommandHandlerRouter",
    "SagaRouter",
    "ProcessManagerRouter",
    "ProjectorRouter",
    "UpcasterRouter",
    "command_handler",
    "saga",
    "process_manager",
    "projector",
    "upcaster",
    "handles",
    "applies",
    "rejected",
    "state_factory",
    "upcasts",
    "CommandHandlerGrpc",
    "SagaGrpc",
    "ProcessManagerGrpc",
    "ProjectorGrpc",
    "UpcasterGrpc",
    "CompensationContext",
    "delegate_to_framework",
    "emit_compensation_events",
    "Destinations",
    "AggregateDispatch",
)


@then("the router binding is exported from the router module")
def _then_router_module() -> None:
    router = importlib.import_module("angzarr_client.router")
    for name in (
        "Router",
        "AggregateDispatch",
        "SagaDispatch",
        "ProcessManagerDispatch",
        "ProjectorDispatch",
        "Rebuilder",
        "Destinations",
        "CodedError",
    ):
        assert hasattr(router, name), f"angzarr_client.router lacks {name}"


@then(
    "the client's root exports no dispatch-engine API: no handler decorators, "
    "Router builders, handler gRPC adapters or compensation helpers"
)
def _then_no_engine_at_root() -> None:
    root = importlib.import_module("angzarr_client")
    leaked = [n for n in _ENGINE_NAMES if hasattr(root, n) or n in root.__all__]
    assert not leaked, f"angzarr_client exports dispatch-engine names: {leaked}"


@then(
    "no exported symbol, module or gRPC service of the client names an example or business concept"
)
def _then_no_business_terms() -> None:
    from tests.business_terms import business_terms_in, package_files

    files = package_files()
    assert any(
        f.name == "host.py" for f in files
    ), "the scan must cover the component host"
    hits = {str(f): business_terms_in(f) for f in files}
    hits = {f: h for f, h in hits.items() if h}
    assert not hits, f"business vocabulary in angzarr_client: {hits}"


@then(
    "the component host serves only framework services and the services an application registers"
)
def _then_host_serves_framework_services() -> None:
    from angzarr_client.host import FRAMEWORK_SERVICES

    assert set(FRAMEWORK_SERVICES) == {
        "io.angzarr.v1.CommandHandlerService",
        "io.angzarr.v1.SagaService",
        "io.angzarr.v1.ProcessManagerService",
        "io.angzarr.v1.ProjectorService",
        "io.angzarr.v1.UpcasterService",
    }


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
