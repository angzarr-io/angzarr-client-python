"""Validation helpers for command handler precondition checks.

Eliminates repeated validation boilerplate across aggregate handlers.

A failing helper raises the router's :class:`~angzarr_client.router.Rejection`,
the business rejection a handler raises itself, so the router answers it
under the helper's key, status and args. (The client's
:class:`~angzarr_client.errors.CommandRejectedError` is what a coordinator
client raises for a rejected command's response; escaping a handler, the
router would answer it as a panic.)

Audit finding #59 (structural error model). Errors carry a SCREAMING_SNAKE
key (from ``angzarr_client.error_codes.codes``) and structured args (keyed
by ``angzarr_client.error_codes.keys``), never text; the English is
rendered from the catalog keyed by the code (decision 14.37). The
structured ``info``-level log emitted alongside surfaces the same fields
for observability.
"""

import logging
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from .error_codes import codes, keys
from .router.errors import GrpcCode, Rejection

_LOG = logging.getLogger(__name__)

# Numeric types accepted by `require_positive` / `require_non_negative`.
# Mirrors Rust's generic `<T: PartialOrd>` bound which accepts any
# orderable numeric type — Python's nearest analog is the union of
# the standard numeric tower (`int | float | Decimal`). P3.1 / audit
# finding #14.
_Numeric = int | float | Decimal


def _reject(
    field: str, predicate: str, status: GrpcCode, code: str, args: dict[str, str]
) -> Rejection:
    """The rejection ``code`` under ``status`` with ``args``, after a
    structured info-level log of it.

    The structured fields ride on the LogRecord's ``extra`` so JSON /
    structured handlers can index them; the formatted message stays
    human-readable for plain-text logs.
    """
    _LOG.info(
        "validation rejection: %s",
        code,
        extra={
            "field": field,
            "predicate": predicate,
            "status_code": status.name,
        },
    )
    return Rejection(code, status, **args)


def require_exists(exists: bool, context: str) -> None:
    """Require that an aggregate exists (caller-supplied predicate).

    Audit #60: takes a precomputed predicate boolean (e.g.
    ``state.is_some()`` / ``bool(state.id)``) so the callsite is
    explicit about what "exists" means. Mirrors Rust's
    ``validation.rs:41`` ``require_exists(exists: bool, context: &str)``.

    Raises :class:`~angzarr_client.router.Rejection` ``ENTITY_NOT_FOUND``,
    NOT_FOUND, with ``context`` — not retryable, since refetching events
    cannot change the outcome. For empty-string checks, use
    :func:`require_not_empty_str`.
    """
    if not exists:
        raise _reject(
            "<entity>",
            "exists",
            GrpcCode.NOT_FOUND,
            codes.ENTITY_NOT_FOUND,
            {keys.CONTEXT: context},
        )


def require_not_exists(exists: bool, context: str) -> None:
    """Require that an aggregate does NOT exist (caller-supplied predicate).

    Audit #60: takes a precomputed predicate boolean. Mirrors Rust's
    ``validation.rs:54`` ``require_not_exists(exists: bool, context: &str)``.

    Raises :class:`~angzarr_client.router.Rejection`
    ``ENTITY_ALREADY_EXISTS``, FAILED_PRECONDITION, with ``context``.
    """
    if exists:
        raise _reject(
            "<entity>",
            "not_exists",
            GrpcCode.FAILED_PRECONDITION,
            codes.ENTITY_ALREADY_EXISTS,
            {keys.CONTEXT: context},
        )


def require_positive(value: _Numeric, field_name: str) -> None:
    """Require that a value is greater than zero.

    Raises :class:`~angzarr_client.router.Rejection` ``VALUE_NOT_POSITIVE``,
    INVALID_ARGUMENT, with ``field``.
    """
    if value <= 0:
        raise _reject(
            field_name,
            "positive",
            GrpcCode.INVALID_ARGUMENT,
            codes.VALUE_NOT_POSITIVE,
            {keys.FIELD: field_name},
        )


def require_non_negative(value: _Numeric, field_name: str) -> None:
    """Require that a value is zero or greater.

    Raises :class:`~angzarr_client.router.Rejection`
    ``VALUE_NOT_NON_NEGATIVE``, INVALID_ARGUMENT, with ``field``.
    """
    if value < 0:
        raise _reject(
            field_name,
            "non_negative",
            GrpcCode.INVALID_ARGUMENT,
            codes.VALUE_NOT_NON_NEGATIVE,
            {keys.FIELD: field_name},
        )


def require_not_empty(items: Sequence[Any], field_name: str) -> None:
    """Require that a sequence has at least one element.

    Raises :class:`~angzarr_client.router.Rejection` ``COLLECTION_EMPTY``,
    INVALID_ARGUMENT, with ``field``.
    """
    if not items:
        raise _reject(
            field_name,
            "not_empty",
            GrpcCode.INVALID_ARGUMENT,
            codes.COLLECTION_EMPTY,
            {keys.FIELD: field_name},
        )


def require_not_empty_str(value: str, field_name: str) -> None:
    """Require that a string is not empty.

    Raises :class:`~angzarr_client.router.Rejection` ``VALUE_EMPTY``,
    INVALID_ARGUMENT, with ``field``.
    """
    if not value:
        raise _reject(
            field_name,
            "not_empty_str",
            GrpcCode.INVALID_ARGUMENT,
            codes.VALUE_EMPTY,
            {keys.FIELD: field_name},
        )


def require_status(actual: str, expected: str, context: str) -> None:
    """Require that the current status matches the expected value.

    Raises :class:`~angzarr_client.router.Rejection` ``STATUS_MISMATCH``,
    FAILED_PRECONDITION, with ``context``.
    """
    if actual != expected:
        raise _reject(
            "status",
            "status_eq",
            GrpcCode.FAILED_PRECONDITION,
            codes.STATUS_MISMATCH,
            {keys.CONTEXT: context},
        )


def require_status_not(actual: str, forbidden: str, context: str) -> None:
    """Require that the current status is NOT the forbidden value.

    Raises :class:`~angzarr_client.router.Rejection` ``STATUS_FORBIDDEN``,
    FAILED_PRECONDITION, with ``context``.
    """
    if actual == forbidden:
        raise _reject(
            "status",
            "status_not",
            GrpcCode.FAILED_PRECONDITION,
            codes.STATUS_FORBIDDEN,
            {keys.CONTEXT: context},
        )
