"""Build-time semantic validation per handler kind.

R1 catches missing required kwargs at decoration time via keyword-only args.
R5 (this module) catches *semantic* errors: empty strings or empty lists in
fields that must be populated.

Duplicate (domain, type_url) across multiple instances is intentionally NOT
an error — the unified Router supports "call both", where all matching
handlers are invoked in registration order and their outputs merged.
"""

from __future__ import annotations

from typing import Any

from .builder import BuildError


def validate_handler(handler: Any) -> None:
    """Raise :class:`BuildError` if the handler's class metadata is incoherent.

    Called once per registered handler at build time. Check the kind
    discriminant (set by the class decorator) and validate kind-specific
    fields.
    """
    cls = type(handler)
    kind: str = cls.__angzarr_kind__
    meta: dict[str, Any] = cls.__angzarr_meta__

    if kind == "command_handler":
        _validate_command_handler(cls.__name__, meta)
    elif kind == "saga":
        _validate_saga(cls.__name__, meta)
    elif kind == "process_manager":
        _validate_process_manager(cls.__name__, meta)
    elif kind == "projector":
        _validate_projector(cls.__name__, meta)
    elif kind == "upcaster":
        _validate_upcaster(cls.__name__, meta)
    else:  # pragma: no cover — guarded earlier by _stamp()
        raise BuildError(f"{cls.__name__}: unknown handler kind {kind!r}")


def _require_non_empty_str(cls_name: str, field: str, value: Any) -> None:
    if not isinstance(value, str) or not value:
        raise BuildError(f"{cls_name}: {field!r} must be a non-empty string")


def _require_non_empty_list(cls_name: str, field: str, value: Any) -> None:
    if not isinstance(value, list) or not value:
        raise BuildError(f"{cls_name}: {field!r} must be a non-empty list")


def _validate_command_handler(cls_name: str, meta: dict[str, Any]) -> None:
    _require_non_empty_str(cls_name, "domain", meta.get("domain"))
    state = meta.get("state")
    if not isinstance(state, type):
        raise BuildError(f"{cls_name}: 'state' must be a type")


def _validate_saga(cls_name: str, meta: dict[str, Any]) -> None:
    _require_non_empty_str(cls_name, "name", meta.get("name"))
    _require_non_empty_str(cls_name, "source", meta.get("source"))
    _require_non_empty_str(cls_name, "target", meta.get("target"))


def _validate_process_manager(cls_name: str, meta: dict[str, Any]) -> None:
    _require_non_empty_str(cls_name, "name", meta.get("name"))
    _require_non_empty_str(cls_name, "pm_domain", meta.get("pm_domain"))
    _require_non_empty_list(cls_name, "sources", meta.get("sources"))
    _require_non_empty_list(cls_name, "targets", meta.get("targets"))
    state = meta.get("state")
    if not isinstance(state, type):
        raise BuildError(f"{cls_name}: 'state' must be a type")


def _validate_projector(cls_name: str, meta: dict[str, Any]) -> None:
    _require_non_empty_str(cls_name, "name", meta.get("name"))
    _require_non_empty_list(cls_name, "domains", meta.get("domains"))


def _validate_upcaster(cls_name: str, meta: dict[str, Any]) -> None:
    _require_non_empty_str(cls_name, "name", meta.get("name"))
    _require_non_empty_str(cls_name, "domain", meta.get("domain"))
