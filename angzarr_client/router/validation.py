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

from angzarr_client.error_codes import codes, keys, messages

from .builder import BuildError


def validate_handler(cls: type) -> None:
    """Raise :class:`BuildError` if the handler class's metadata is incoherent.

    Called once per registered handler class at build time. Check the kind
    discriminant (set by the class decorator) and validate kind-specific
    fields. Operates purely on class-level metadata — no instance required.

    Audit #72: BuildError raise sites use the structural error model —
    ``code`` from ``codes``, static ``message`` from ``messages``, and
    runtime context (handler class, field name) in ``details``.
    """
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
        raise BuildError(
            messages.HANDLER_UNKNOWN_KIND,
            code=codes.HANDLER_UNKNOWN_KIND,
            details={
                keys.HANDLER_CLASS: cls.__name__,
                keys.HANDLER_KIND: str(kind),
            },
        )


def _require_non_empty_str(cls_name: str, field: str, value: Any) -> None:
    if not isinstance(value, str) or not value:
        raise BuildError(
            messages.HANDLER_FIELD_EMPTY_STRING,
            code=codes.HANDLER_FIELD_EMPTY_STRING,
            details={keys.HANDLER_CLASS: cls_name, keys.FIELD: field},
        )


def _require_non_empty_list(cls_name: str, field: str, value: Any) -> None:
    if not isinstance(value, list) or not value:
        raise BuildError(
            messages.HANDLER_FIELD_EMPTY_LIST,
            code=codes.HANDLER_FIELD_EMPTY_LIST,
            details={keys.HANDLER_CLASS: cls_name, keys.FIELD: field},
        )


def _require_state_is_type(cls_name: str, state: Any) -> None:
    if not isinstance(state, type):
        raise BuildError(
            messages.HANDLER_STATE_NOT_TYPE,
            code=codes.HANDLER_STATE_NOT_TYPE,
            details={keys.HANDLER_CLASS: cls_name, keys.FIELD: "state"},
        )


def _validate_command_handler(cls_name: str, meta: dict[str, Any]) -> None:
    _require_non_empty_str(cls_name, "domain", meta.get("domain"))
    _require_state_is_type(cls_name, meta.get("state"))


def _validate_saga(cls_name: str, meta: dict[str, Any]) -> None:
    _require_non_empty_str(cls_name, "name", meta.get("name"))
    _require_non_empty_str(cls_name, "source", meta.get("source"))
    _require_non_empty_str(cls_name, "target", meta.get("target"))


def _validate_process_manager(cls_name: str, meta: dict[str, Any]) -> None:
    _require_non_empty_str(cls_name, "name", meta.get("name"))
    _require_non_empty_str(cls_name, "pm_domain", meta.get("pm_domain"))
    _require_non_empty_list(cls_name, "sources", meta.get("sources"))
    _require_non_empty_list(cls_name, "targets", meta.get("targets"))
    _require_state_is_type(cls_name, meta.get("state"))


def _validate_projector(cls_name: str, meta: dict[str, Any]) -> None:
    _require_non_empty_str(cls_name, "name", meta.get("name"))
    _require_non_empty_list(cls_name, "domains", meta.get("domains"))


def _validate_upcaster(cls_name: str, meta: dict[str, Any]) -> None:
    _require_non_empty_str(cls_name, "name", meta.get("name"))
    _require_non_empty_str(cls_name, "domain", meta.get("domain"))
