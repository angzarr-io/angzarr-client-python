"""Tests for validation helpers.

Audit finding #59: validation rejections are the router's Rejection under
stable codes (from ``angzarr_client.error_codes.codes``), English rendered
from the catalog by the code, and structured args (keyed by ``keys``). Tests
assert against those constants rather than against substring patterns.
"""

import logging
from collections.abc import Callable
from typing import NamedTuple

import pytest

from angzarr_client.diagnostic import render
from angzarr_client.error_codes import codes, keys
from angzarr_client.router import GrpcCode, Rejection
from angzarr_client.validation import (
    require_exists,
    require_non_negative,
    require_not_empty,
    require_not_empty_str,
    require_not_exists,
    require_positive,
    require_status,
    require_status_not,
)


class TestRequireExists:
    # Audit #60: predicate-bool shape, mirrors Rust validation.rs:41.
    def test_passes_when_true(self):
        require_exists(True, "context")

    def test_fails_when_false(self):
        with pytest.raises(Rejection) as exc:
            require_exists(False, "Player does not exist")
        assert exc.value.code == codes.ENTITY_NOT_FOUND
        assert render(exc.value.code, exc.value.extras) == "entity not found"
        assert exc.value.extras[keys.CONTEXT] == "Player does not exist"


class TestRequireNotExists:
    def test_passes_when_false(self):
        require_not_exists(False, "context")

    def test_fails_when_true(self):
        with pytest.raises(Rejection) as exc:
            require_not_exists(True, "Player already exists")
        assert exc.value.code == codes.ENTITY_ALREADY_EXISTS
        assert render(exc.value.code, exc.value.extras) == "entity already exists"
        assert exc.value.extras[keys.CONTEXT] == "Player already exists"


class TestRequirePositive:
    def test_passes(self):
        require_positive(1, "amount")
        require_positive(100, "amount")

    def test_fails_on_zero(self):
        with pytest.raises(Rejection) as exc:
            require_positive(0, "amount")
        assert exc.value.code == codes.VALUE_NOT_POSITIVE
        assert render(exc.value.code, exc.value.extras) == "amount must be positive"
        assert exc.value.extras[keys.FIELD] == "amount"

    def test_fails_on_negative(self):
        with pytest.raises(Rejection) as exc:
            require_positive(-1, "amount")
        assert exc.value.code == codes.VALUE_NOT_POSITIVE

    def test_accepts_float(self):
        """P3.1 / audit finding #14: matches Rust's `<T: PartialOrd>`."""
        require_positive(0.5, "amount")
        with pytest.raises(Rejection) as exc:
            require_positive(0.0, "amount")
        assert exc.value.code == codes.VALUE_NOT_POSITIVE
        with pytest.raises(Rejection):
            require_positive(-0.001, "amount")

    def test_accepts_decimal(self):
        from decimal import Decimal

        require_positive(Decimal("0.01"), "amount")
        with pytest.raises(Rejection):
            require_positive(Decimal(0), "amount")
        with pytest.raises(Rejection):
            require_positive(Decimal(-1), "amount")


class TestRequireNonNegative:
    def test_passes(self):
        require_non_negative(0, "balance")
        require_non_negative(1, "balance")

    def test_fails(self):
        with pytest.raises(Rejection) as exc:
            require_non_negative(-1, "balance")
        assert exc.value.code == codes.VALUE_NOT_NON_NEGATIVE
        assert (
            render(exc.value.code, exc.value.extras) == "balance must be non-negative"
        )
        assert exc.value.extras[keys.FIELD] == "balance"

    def test_accepts_float_and_decimal(self):
        from decimal import Decimal

        require_non_negative(0.0, "balance")
        require_non_negative(0.5, "balance")
        require_non_negative(Decimal(0), "balance")
        with pytest.raises(Rejection) as exc:
            require_non_negative(-0.001, "balance")
        assert exc.value.code == codes.VALUE_NOT_NON_NEGATIVE
        with pytest.raises(Rejection):
            require_non_negative(Decimal("-0.5"), "balance")


class TestRequireNotEmpty:
    def test_passes(self):
        require_not_empty([1, 2, 3], "items")

    def test_fails(self):
        with pytest.raises(Rejection) as exc:
            require_not_empty([], "items")
        assert exc.value.code == codes.COLLECTION_EMPTY
        assert render(exc.value.code, exc.value.extras) == "items must not be empty"
        assert exc.value.extras[keys.FIELD] == "items"


class TestRequireNotEmptyStr:
    def test_passes(self):
        require_not_empty_str("hello", "name")

    def test_fails(self):
        with pytest.raises(Rejection) as exc:
            require_not_empty_str("", "name")
        assert exc.value.code == codes.VALUE_EMPTY
        assert render(exc.value.code, exc.value.extras) == "name must not be empty"
        assert exc.value.extras[keys.FIELD] == "name"


class TestRequireStatus:
    def test_passes(self):
        require_status("active", "active", "context")

    def test_fails(self):
        with pytest.raises(Rejection) as exc:
            require_status("pending", "active", "must be active")
        assert exc.value.code == codes.STATUS_MISMATCH
        assert (
            render(exc.value.code, exc.value.extras) == "status does not match expected"
        )


class TestRequireStatusNot:
    def test_passes(self):
        require_status_not("active", "checked_out", "context")

    def test_fails(self):
        with pytest.raises(Rejection) as exc:
            require_status_not("checked_out", "checked_out", "already checked out")
        assert exc.value.code == codes.STATUS_FORBIDDEN
        assert (
            render(exc.value.code, exc.value.extras) == "status is the forbidden value"
        )


class TestStructuredLogging:
    """Audit finding #59: validation rejections emit a structured info log
    with `field`, `predicate`, and `status_code` fields."""

    def test_require_positive_logs_structured_fields(self, caplog):
        with (
            caplog.at_level(logging.INFO, logger="angzarr_client.validation"),
            pytest.raises(Rejection),
        ):
            require_positive(0, "amount")

        records = [r for r in caplog.records if r.name == "angzarr_client.validation"]
        assert len(records) == 1
        rec = records[0]
        assert rec.field == "amount"
        assert rec.predicate == "positive"
        assert rec.status_code == "INVALID_ARGUMENT"
        assert codes.VALUE_NOT_POSITIVE in rec.getMessage()

    def test_require_non_negative_logs_structured_fields(self, caplog):
        with (
            caplog.at_level(logging.INFO, logger="angzarr_client.validation"),
            pytest.raises(Rejection),
        ):
            require_non_negative(-1, "balance")

        records = [r for r in caplog.records if r.name == "angzarr_client.validation"]
        assert len(records) == 1
        assert records[0].field == "balance"
        assert records[0].predicate == "non_negative"
        assert records[0].status_code == "INVALID_ARGUMENT"

    def test_require_not_empty_logs_structured_fields(self, caplog):
        with (
            caplog.at_level(logging.INFO, logger="angzarr_client.validation"),
            pytest.raises(Rejection),
        ):
            require_not_empty([], "items")

        records = [r for r in caplog.records if r.name == "angzarr_client.validation"]
        assert len(records) == 1
        assert records[0].field == "items"
        assert records[0].predicate == "not_empty"
        assert records[0].status_code == "INVALID_ARGUMENT"

    def test_require_exists_logs_not_found(self, caplog):
        with (
            caplog.at_level(logging.INFO, logger="angzarr_client.validation"),
            pytest.raises(Rejection),
        ):
            require_exists(False, "entity")

        records = [r for r in caplog.records if r.name == "angzarr_client.validation"]
        assert len(records) == 1
        assert records[0].status_code == "NOT_FOUND"
        assert records[0].predicate == "exists"

    def test_passing_validation_emits_no_log(self, caplog):
        with caplog.at_level(logging.INFO, logger="angzarr_client.validation"):
            require_positive(5, "amount")
            require_non_negative(0, "balance")
            require_not_empty([1], "items")

        records = [r for r in caplog.records if r.name == "angzarr_client.validation"]
        assert records == []


class _Rejection(NamedTuple):
    """One validator's rejection: the call that raises it, the error it
    raises, and the info record it logs."""

    call: Callable[[], None]
    code: str
    message: str
    status_code: str
    details: dict[str, str]
    log_field: str
    log_predicate: str


_REJECTIONS = [
    _Rejection(
        lambda: require_exists(False, "player lookup"),
        codes.ENTITY_NOT_FOUND,
        "entity not found",
        "NOT_FOUND",
        {keys.CONTEXT: "player lookup"},
        "<entity>",
        "exists",
    ),
    _Rejection(
        lambda: require_not_exists(True, "player create"),
        codes.ENTITY_ALREADY_EXISTS,
        "entity already exists",
        "FAILED_PRECONDITION",
        {keys.CONTEXT: "player create"},
        "<entity>",
        "not_exists",
    ),
    _Rejection(
        lambda: require_positive(0, "amount"),
        codes.VALUE_NOT_POSITIVE,
        "amount must be positive",
        "INVALID_ARGUMENT",
        {keys.FIELD: "amount"},
        "amount",
        "positive",
    ),
    _Rejection(
        lambda: require_non_negative(-1, "balance"),
        codes.VALUE_NOT_NON_NEGATIVE,
        "balance must be non-negative",
        "INVALID_ARGUMENT",
        {keys.FIELD: "balance"},
        "balance",
        "non_negative",
    ),
    _Rejection(
        lambda: require_not_empty([], "items"),
        codes.COLLECTION_EMPTY,
        "items must not be empty",
        "INVALID_ARGUMENT",
        {keys.FIELD: "items"},
        "items",
        "not_empty",
    ),
    _Rejection(
        lambda: require_not_empty_str("", "name"),
        codes.VALUE_EMPTY,
        "name must not be empty",
        "INVALID_ARGUMENT",
        {keys.FIELD: "name"},
        "name",
        "not_empty_str",
    ),
    _Rejection(
        lambda: require_status("pending", "active", "must be active"),
        codes.STATUS_MISMATCH,
        "status does not match expected",
        "FAILED_PRECONDITION",
        {keys.CONTEXT: "must be active"},
        "status",
        "status_eq",
    ),
    _Rejection(
        lambda: require_status_not("closed", "closed", "already closed"),
        codes.STATUS_FORBIDDEN,
        "status is the forbidden value",
        "FAILED_PRECONDITION",
        {keys.CONTEXT: "already closed"},
        "status",
        "status_not",
    ),
]

_REJECTION_IDS = [
    "exists",
    "not_exists",
    "positive",
    "non_negative",
    "not_empty",
    "not_empty_str",
    "status",
    "status_not",
]


class TestRejectionContract:
    """Every validator rejects with the router's Rejection: a fixed code, its
    rendered message, a gRPC status and structured args, and emits exactly
    one info record carrying the same field / predicate / status_code."""

    @pytest.mark.parametrize("rejection", _REJECTIONS, ids=_REJECTION_IDS)
    def test_raised_error(self, rejection):
        with pytest.raises(Rejection) as exc:
            rejection.call()
        err = exc.value
        assert err.code == rejection.code
        assert render(err.code, err.extras) == rejection.message
        assert str(err).startswith(rejection.code)
        assert err.grpc == GrpcCode[rejection.status_code]
        assert err.extras == rejection.details

    @pytest.mark.parametrize("rejection", _REJECTIONS, ids=_REJECTION_IDS)
    def test_structured_log_record(self, caplog, rejection):
        with (
            caplog.at_level(logging.INFO, logger="angzarr_client.validation"),
            pytest.raises(Rejection),
        ):
            rejection.call()

        records = [r for r in caplog.records if r.name == "angzarr_client.validation"]
        assert len(records) == 1
        rec = records[0]
        assert rec.levelno == logging.INFO
        assert rec.getMessage() == f"validation rejection: {rejection.code}"
        assert rec.field == rejection.log_field
        assert rec.predicate == rejection.log_predicate
        assert rec.status_code == rejection.status_code
