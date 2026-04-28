"""Validation helpers for command handler precondition checks.

Eliminates repeated validation boilerplate across aggregate handlers.

Audit finding #59 (structural error model). Errors carry a SCREAMING_SNAKE
``code`` (from ``angzarr_client.error_codes.codes``), a static
``message`` (from ``angzarr_client.error_codes.messages``), and
structured ``details`` (keyed by ``angzarr_client.error_codes.keys``).
The structured ``info``-level log emitted alongside surfaces the same
fields for observability.
"""

import logging
from collections.abc import Sequence
from decimal import Decimal
from typing import Any, Union

from .error_codes import codes, keys, messages
from .errors import CommandRejectedError

_LOG = logging.getLogger(__name__)

# Numeric types accepted by `require_positive` / `require_non_negative`.
# Mirrors Rust's generic `<T: PartialOrd>` bound which accepts any
# orderable numeric type — Python's nearest analog is the union of
# the standard numeric tower (`int | float | Decimal`). P3.1 / audit
# finding #14.
_Numeric = Union[int, float, Decimal]


def _log_rejection(field: str, predicate: str, status_code: str, message: str) -> None:
    """Emit a structured info-level log for a validation rejection.

    The structured fields ride on the LogRecord's ``extra`` so JSON /
    structured handlers can index them; the formatted message stays
    human-readable for plain-text logs.
    """
    _LOG.info(
        "validation rejection: %s",
        message,
        extra={
            "field": field,
            "predicate": predicate,
            "status_code": status_code,
        },
    )


def require_exists(field: str, context: str) -> None:
    """Require that a field is non-empty (entity exists).

    Raises a NOT_FOUND rejection — not retryable, since refetching events
    cannot change the outcome.
    """
    if not field:
        _log_rejection(
            field="<entity>",
            predicate="exists",
            status_code="NOT_FOUND",
            message=messages.ENTITY_NOT_FOUND,
        )
        raise CommandRejectedError.not_found(
            codes.ENTITY_NOT_FOUND,
            messages.ENTITY_NOT_FOUND,
            {keys.CONTEXT: context},
        )


def require_not_exists(field: str, context: str) -> None:
    """Require that a field is empty (entity does not yet exist)."""
    if field:
        _log_rejection(
            field="<entity>",
            predicate="not_exists",
            status_code="FAILED_PRECONDITION",
            message=messages.ENTITY_ALREADY_EXISTS,
        )
        raise CommandRejectedError.precondition_failed(
            codes.ENTITY_ALREADY_EXISTS,
            messages.ENTITY_ALREADY_EXISTS,
            {keys.CONTEXT: context},
        )


def require_positive(value: _Numeric, field_name: str) -> None:
    """Require that a value is greater than zero."""
    if value <= 0:
        _log_rejection(
            field=field_name,
            predicate="positive",
            status_code="INVALID_ARGUMENT",
            message=messages.VALUE_NOT_POSITIVE,
        )
        raise CommandRejectedError.invalid_argument(
            codes.VALUE_NOT_POSITIVE,
            messages.VALUE_NOT_POSITIVE,
            {keys.FIELD: field_name},
        )


def require_non_negative(value: _Numeric, field_name: str) -> None:
    """Require that a value is zero or greater."""
    if value < 0:
        _log_rejection(
            field=field_name,
            predicate="non_negative",
            status_code="INVALID_ARGUMENT",
            message=messages.VALUE_NOT_NON_NEGATIVE,
        )
        raise CommandRejectedError.invalid_argument(
            codes.VALUE_NOT_NON_NEGATIVE,
            messages.VALUE_NOT_NON_NEGATIVE,
            {keys.FIELD: field_name},
        )


def require_not_empty(items: Sequence[Any], field_name: str) -> None:
    """Require that a sequence has at least one element."""
    if not items:
        _log_rejection(
            field=field_name,
            predicate="not_empty",
            status_code="INVALID_ARGUMENT",
            message=messages.COLLECTION_EMPTY,
        )
        raise CommandRejectedError.invalid_argument(
            codes.COLLECTION_EMPTY,
            messages.COLLECTION_EMPTY,
            {keys.FIELD: field_name},
        )


def require_not_empty_str(value: str, field_name: str) -> None:
    """Require that a string is not empty."""
    if not value:
        _log_rejection(
            field=field_name,
            predicate="not_empty_str",
            status_code="INVALID_ARGUMENT",
            message=messages.VALUE_EMPTY,
        )
        raise CommandRejectedError.invalid_argument(
            codes.VALUE_EMPTY,
            messages.VALUE_EMPTY,
            {keys.FIELD: field_name},
        )


def require_status(actual: str, expected: str, context: str) -> None:
    """Require that the current status matches the expected value."""
    if actual != expected:
        _log_rejection(
            field="status",
            predicate="status_eq",
            status_code="FAILED_PRECONDITION",
            message=messages.STATUS_MISMATCH,
        )
        raise CommandRejectedError.precondition_failed(
            codes.STATUS_MISMATCH,
            messages.STATUS_MISMATCH,
            {keys.CONTEXT: context},
        )


def require_status_not(actual: str, forbidden: str, context: str) -> None:
    """Require that the current status is NOT the forbidden value."""
    if actual == forbidden:
        _log_rejection(
            field="status",
            predicate="status_not",
            status_code="FAILED_PRECONDITION",
            message=messages.STATUS_FORBIDDEN,
        )
        raise CommandRejectedError.precondition_failed(
            codes.STATUS_FORBIDDEN,
            messages.STATUS_FORBIDDEN,
            {keys.CONTEXT: context},
        )
