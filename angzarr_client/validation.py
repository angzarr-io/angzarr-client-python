"""Validation helpers for command handler precondition checks.

Eliminates repeated validation boilerplate across aggregate handlers.
"""

from collections.abc import Sequence
from decimal import Decimal
from typing import Any, Union

from .errors import CommandRejectedError

# Numeric types accepted by `require_positive` / `require_non_negative`.
# Mirrors Rust's generic `<T: PartialOrd>` bound which accepts any
# orderable numeric type — Python's nearest analog is the union of
# the standard numeric tower (`int | float | Decimal`). P3.1 / audit
# finding #14.
_Numeric = Union[int, float, Decimal]


def require_exists(field: str, error_msg: str) -> None:
    """Require that a field is non-empty (entity exists).

    Raises a NOT_FOUND rejection — not retryable, since refetching events
    cannot change the outcome.
    """
    if not field:
        raise CommandRejectedError.not_found(error_msg)


def require_not_exists(field: str, error_msg: str) -> None:
    """Require that a field is empty (entity does not yet exist)."""
    if field:
        raise CommandRejectedError(error_msg)


def require_positive(value: _Numeric, error_msg: str) -> None:
    """Require that a value is greater than zero.

    Accepts `int`, `float`, or `Decimal` — matches Rust's
    `<T: PartialOrd>` generic bound (`validation.rs:47`).
    """
    if value <= 0:
        raise CommandRejectedError.invalid_argument(error_msg)


def require_non_negative(value: _Numeric, error_msg: str) -> None:
    """Require that a value is zero or greater.

    Accepts `int`, `float`, or `Decimal` — see :func:`require_positive`.
    """
    if value < 0:
        raise CommandRejectedError.invalid_argument(error_msg)


def require_not_empty(items: Sequence[Any], error_msg: str) -> None:
    """Require that a sequence has at least one element."""
    if not items:
        raise CommandRejectedError.invalid_argument(error_msg)


def require_not_empty_str(value: str, field_name: str) -> None:
    """Require that a string is not empty.

    Args:
        value: The string value to check
        field_name: The name of the field for the error message
    """
    if not value:
        raise CommandRejectedError.invalid_argument(f"{field_name} must not be empty")


def require_status(actual: str, expected: str, error_msg: str) -> None:
    """Require that the current status matches the expected value."""
    if actual != expected:
        raise CommandRejectedError(error_msg)


def require_status_not(actual: str, forbidden: str, error_msg: str) -> None:
    """Require that the current status is NOT the forbidden value."""
    if actual == forbidden:
        raise CommandRejectedError(error_msg)
