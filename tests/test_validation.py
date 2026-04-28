"""Tests for validation helpers.

Audit finding #59: validation rejections use stable codes (from
``angzarr_client.error_codes.codes``), static messages (from ``messages``),
and structured details (keyed by ``keys``). Tests assert against those
constants rather than against substring patterns.
"""

import logging

import pytest

from angzarr_client.error_codes import codes, keys, messages
from angzarr_client.errors import CommandRejectedError
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
        with pytest.raises(CommandRejectedError) as exc:
            require_exists(False, "Player does not exist")
        assert exc.value.code == codes.ENTITY_NOT_FOUND
        assert exc.value.message == messages.ENTITY_NOT_FOUND
        assert exc.value.details[keys.CONTEXT] == "Player does not exist"


class TestRequireNotExists:
    def test_passes_when_false(self):
        require_not_exists(False, "context")

    def test_fails_when_true(self):
        with pytest.raises(CommandRejectedError) as exc:
            require_not_exists(True, "Player already exists")
        assert exc.value.code == codes.ENTITY_ALREADY_EXISTS
        assert exc.value.message == messages.ENTITY_ALREADY_EXISTS
        assert exc.value.details[keys.CONTEXT] == "Player already exists"


class TestRequirePositive:
    def test_passes(self):
        require_positive(1, "amount")
        require_positive(100, "amount")

    def test_fails_on_zero(self):
        with pytest.raises(CommandRejectedError) as exc:
            require_positive(0, "amount")
        assert exc.value.code == codes.VALUE_NOT_POSITIVE
        assert exc.value.message == messages.VALUE_NOT_POSITIVE
        assert exc.value.details[keys.FIELD] == "amount"

    def test_fails_on_negative(self):
        with pytest.raises(CommandRejectedError) as exc:
            require_positive(-1, "amount")
        assert exc.value.code == codes.VALUE_NOT_POSITIVE

    def test_accepts_float(self):
        """P3.1 / audit finding #14: matches Rust's `<T: PartialOrd>`."""
        require_positive(0.5, "amount")
        with pytest.raises(CommandRejectedError) as exc:
            require_positive(0.0, "amount")
        assert exc.value.code == codes.VALUE_NOT_POSITIVE
        with pytest.raises(CommandRejectedError):
            require_positive(-0.001, "amount")

    def test_accepts_decimal(self):
        from decimal import Decimal

        require_positive(Decimal("0.01"), "amount")
        with pytest.raises(CommandRejectedError):
            require_positive(Decimal("0"), "amount")
        with pytest.raises(CommandRejectedError):
            require_positive(Decimal("-1"), "amount")


class TestRequireNonNegative:
    def test_passes(self):
        require_non_negative(0, "balance")
        require_non_negative(1, "balance")

    def test_fails(self):
        with pytest.raises(CommandRejectedError) as exc:
            require_non_negative(-1, "balance")
        assert exc.value.code == codes.VALUE_NOT_NON_NEGATIVE
        assert exc.value.message == messages.VALUE_NOT_NON_NEGATIVE
        assert exc.value.details[keys.FIELD] == "balance"

    def test_accepts_float_and_decimal(self):
        from decimal import Decimal

        require_non_negative(0.0, "balance")
        require_non_negative(0.5, "balance")
        require_non_negative(Decimal("0"), "balance")
        with pytest.raises(CommandRejectedError) as exc:
            require_non_negative(-0.001, "balance")
        assert exc.value.code == codes.VALUE_NOT_NON_NEGATIVE
        with pytest.raises(CommandRejectedError):
            require_non_negative(Decimal("-0.5"), "balance")


class TestRequireNotEmpty:
    def test_passes(self):
        require_not_empty([1, 2, 3], "items")

    def test_fails(self):
        with pytest.raises(CommandRejectedError) as exc:
            require_not_empty([], "items")
        assert exc.value.code == codes.COLLECTION_EMPTY
        assert exc.value.message == messages.COLLECTION_EMPTY
        assert exc.value.details[keys.FIELD] == "items"


class TestRequireNotEmptyStr:
    def test_passes(self):
        require_not_empty_str("hello", "name")

    def test_fails(self):
        with pytest.raises(CommandRejectedError) as exc:
            require_not_empty_str("", "name")
        assert exc.value.code == codes.VALUE_EMPTY
        assert exc.value.message == messages.VALUE_EMPTY
        assert exc.value.details[keys.FIELD] == "name"


class TestRequireStatus:
    def test_passes(self):
        require_status("active", "active", "context")

    def test_fails(self):
        with pytest.raises(CommandRejectedError) as exc:
            require_status("pending", "active", "must be active")
        assert exc.value.code == codes.STATUS_MISMATCH
        assert exc.value.message == messages.STATUS_MISMATCH


class TestRequireStatusNot:
    def test_passes(self):
        require_status_not("active", "checked_out", "context")

    def test_fails(self):
        with pytest.raises(CommandRejectedError) as exc:
            require_status_not("checked_out", "checked_out", "already checked out")
        assert exc.value.code == codes.STATUS_FORBIDDEN
        assert exc.value.message == messages.STATUS_FORBIDDEN


class TestStructuredLogging:
    """Audit finding #59: validation rejections emit a structured info log
    with `field`, `predicate`, and `status_code` fields."""

    def test_require_positive_logs_structured_fields(self, caplog):
        with caplog.at_level(logging.INFO, logger="angzarr_client.validation"):
            with pytest.raises(CommandRejectedError):
                require_positive(0, "amount")

        records = [r for r in caplog.records if r.name == "angzarr_client.validation"]
        assert len(records) == 1
        rec = records[0]
        assert rec.field == "amount"
        assert rec.predicate == "positive"
        assert rec.status_code == "INVALID_ARGUMENT"
        assert messages.VALUE_NOT_POSITIVE in rec.getMessage()

    def test_require_non_negative_logs_structured_fields(self, caplog):
        with caplog.at_level(logging.INFO, logger="angzarr_client.validation"):
            with pytest.raises(CommandRejectedError):
                require_non_negative(-1, "balance")

        records = [r for r in caplog.records if r.name == "angzarr_client.validation"]
        assert len(records) == 1
        assert records[0].field == "balance"
        assert records[0].predicate == "non_negative"
        assert records[0].status_code == "INVALID_ARGUMENT"

    def test_require_not_empty_logs_structured_fields(self, caplog):
        with caplog.at_level(logging.INFO, logger="angzarr_client.validation"):
            with pytest.raises(CommandRejectedError):
                require_not_empty([], "items")

        records = [r for r in caplog.records if r.name == "angzarr_client.validation"]
        assert len(records) == 1
        assert records[0].field == "items"
        assert records[0].predicate == "not_empty"
        assert records[0].status_code == "INVALID_ARGUMENT"

    def test_require_exists_logs_not_found(self, caplog):
        with caplog.at_level(logging.INFO, logger="angzarr_client.validation"):
            with pytest.raises(CommandRejectedError):
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
