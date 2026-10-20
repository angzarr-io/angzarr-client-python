"""Argument parsing shared by the clients and builders (private)."""

from __future__ import annotations

from uuid import UUID as PyUUID

from .error_codes import codes, keys, messages
from .errors import InvalidArgumentError


def _as_uuid(value: PyUUID | str, field: str) -> PyUUID:
    """``value`` as a UUID: a UUID as given, or a UUID's text form parsed.
    Anything else is refused with ``PROTO_UUID_INVALID``, naming ``field``."""
    if isinstance(value, PyUUID):
        return value
    try:
        return PyUUID(value)
    except (TypeError, ValueError) as exc:
        raise InvalidArgumentError(
            messages.PROTO_UUID_INVALID,
            code=codes.PROTO_UUID_INVALID,
            details={keys.FIELD: field, keys.INPUT: value},
        ) from exc
