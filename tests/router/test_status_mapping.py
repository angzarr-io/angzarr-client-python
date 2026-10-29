"""The binding's error model (decision 14.37: a key and an argument map,
never text): CodedError construction, the four classes the status decides,
what a handler fails with (Rejection and Immediate, business answers; Bug
and RetryableBug, defects), the any_decode_error helper, and the
CodedError <-> google.rpc.Status (with ErrorInfo) mapping that crosses the
FFI in both directions."""

from __future__ import annotations

import logging

import pytest
from google.protobuf import any_pb2
from google.rpc import error_details_pb2, status_pb2

import angzarr_client.router as router_module
from angzarr_client.router import (
    Bug,
    CodedError,
    ErrorClass,
    FactRecord,
    GrpcCode,
    Immediate,
    Rejection,
    RetryableBug,
    any_decode_error,
    error_class,
)
from angzarr_client.router._dispatch import (
    _decode_status,
    _error_status,
)


def _status_with_info(
    code: int, message: str, reason: str, metadata: dict | None = None
) -> bytes:
    info = error_details_pb2.ErrorInfo(
        reason=reason, domain="angzarr.io", metadata=metadata or {}
    )
    detail = any_pb2.Any()
    detail.Pack(info)
    return status_pb2.Status(
        code=code, message=message, details=[detail]
    ).SerializeToString()


def _parse(data: bytes) -> tuple[status_pb2.Status, error_details_pb2.ErrorInfo]:
    status = status_pb2.Status.FromString(data)
    assert len(status.details) == 1
    info = error_details_pb2.ErrorInfo()
    assert status.details[0].Unpack(info)
    return status, info


# --- CodedError ---


def test_a_bare_coded_error_is_an_uncoded_internal_failure():
    err = CodedError()

    assert err.code == ""
    assert err.grpc == GrpcCode.INTERNAL
    assert err.extras == {}
    assert str(err) == ""


def test_a_coded_error_carries_no_text():
    # Decision 14.37: a key and an argument map, never rendered English.
    assert not hasattr(CodedError("VALUE_NOT_POSITIVE"), "message")


def test_coded_error_keeps_a_copy_of_its_extras():
    extras = {"domain": "ledger"}
    err = CodedError("NOT_THERE", GrpcCode.NOT_FOUND, extras)
    extras["domain"] = "changed"

    assert err.extras == {"domain": "ledger"}
    assert err.grpc == 5


def test_coded_error_text_is_its_code_then_its_args():
    # In process too, an error's text is its code then ` name=value` per arg
    # (decision 14.37, owner's clarifications 2026-10-29).
    assert str(CodedError("VALUE_NOT_POSITIVE")) == "VALUE_NOT_POSITIVE"
    assert (
        str(CodedError("SEAT_TAKEN", extras={"seat": "3", "table": "t1"}))
        == "SEAT_TAKEN seat=3 table=t1"
    )


def test_a_rejection_is_a_failed_precondition_carrying_its_key_and_args():
    err = Rejection("VALUE_NOT_POSITIVE", amount=0)

    assert isinstance(err, CodedError)
    assert err.code == "VALUE_NOT_POSITIVE"
    assert err.grpc == GrpcCode.FAILED_PRECONDITION
    assert err.extras == {"amount": "0"}
    assert err.error_class is ErrorClass.BusinessFatal
    assert str(err) == "VALUE_NOT_POSITIVE amount=0"


# A rejection's status: one of the business set (the owner's ruling,
# 2026-10-29), FAILED_PRECONDITION unless given. The rejection keeps the
# status it was given; it crosses the C ABI under a status the pinned
# angzarr-router carries.


def _rejected_under(status, crosses):
    err = Rejection("SEAT_TAKEN", status, seat=3)

    assert err.code == "SEAT_TAKEN"
    assert err.grpc == status
    assert err.error_class is ErrorClass.BusinessFatal
    assert err.extras == {"seat": "3"}
    data, ret = _error_status(err)
    wire, info = _parse(data)
    assert (wire.code, ret) == (int(crosses), -int(crosses))
    assert wire.message == "SEAT_TAKEN"
    assert (info.reason, dict(info.metadata)) == ("SEAT_TAKEN", {"seat": "3"})


def test_a_rejection_under_failed_precondition_crosses_as_it():
    _rejected_under(GrpcCode.FAILED_PRECONDITION, GrpcCode.FAILED_PRECONDITION)


def test_a_rejection_under_invalid_argument_crosses_as_it():
    _rejected_under(GrpcCode.INVALID_ARGUMENT, GrpcCode.INVALID_ARGUMENT)


def test_a_rejection_under_not_found_crosses_as_it():
    _rejected_under(GrpcCode.NOT_FOUND, GrpcCode.NOT_FOUND)


# The pinned angzarr-router's GrpcCode has no ALREADY_EXISTS, OUT_OF_RANGE
# or PERMISSION_DENIED, and turns a status it has no variant for INTERNAL,
# a bug: a rejection under one crosses as FAILED_PRECONDITION, still a
# refusal.


def test_a_rejection_under_already_exists_crosses_as_failed_precondition():
    assert GrpcCode.ALREADY_EXISTS == 6
    _rejected_under(GrpcCode.ALREADY_EXISTS, GrpcCode.FAILED_PRECONDITION)


def test_a_rejection_under_out_of_range_crosses_as_failed_precondition():
    assert GrpcCode.OUT_OF_RANGE == 11
    _rejected_under(GrpcCode.OUT_OF_RANGE, GrpcCode.FAILED_PRECONDITION)


def test_a_rejection_under_permission_denied_crosses_as_failed_precondition():
    assert GrpcCode.PERMISSION_DENIED == 7
    _rejected_under(GrpcCode.PERMISSION_DENIED, GrpcCode.FAILED_PRECONDITION)


def test_a_rejections_status_may_be_its_number():
    assert Rejection("SEAT_TAKEN", 5).grpc == GrpcCode.NOT_FOUND


@pytest.mark.parametrize(
    "status",
    [
        GrpcCode.INTERNAL,
        GrpcCode.DATA_LOSS,
        GrpcCode.ABORTED,
        GrpcCode.UNIMPLEMENTED,
        0,  # OK
        2,  # UNKNOWN
        4,  # DEADLINE_EXCEEDED
        8,  # RESOURCE_EXHAUSTED
        14,  # UNAVAILABLE
    ],
)
def test_a_rejection_outside_the_business_set_is_refused_at_construction(status):
    with pytest.raises(ValueError, match="business"):
        Rejection("SEAT_TAKEN", status)


def test_an_arg_named_status_is_an_arg_not_the_rejections_status():
    err = Rejection("SEAT_TAKEN", status="open")

    assert err.grpc == GrpcCode.FAILED_PRECONDITION
    assert err.extras == {"status": "open"}


# --- the immediate return (ABORTED, a handler's own key) ---


def test_an_immediate_return_is_aborted_under_its_own_key():
    # The owner's rulings, 2026-10-29 (latest): "re-read and re-decide", as
    # angzarr-router's CodedError::immediate(key, args).
    err = Immediate("QUOTE_MOVED", quote=7)

    assert isinstance(err, CodedError)
    assert err.code == "QUOTE_MOVED"
    assert err.grpc == GrpcCode.ABORTED
    assert err.extras == {"quote": "7"}
    assert err.error_class is ErrorClass.BusinessImmediate


def test_an_immediate_return_crosses_as_aborted_with_its_key_as_reason():
    data, ret = _error_status(Immediate("QUOTE_MOVED", quote=7))
    status, info = _parse(data)

    assert (status.code, ret) == (10, -10)
    assert status.message == "QUOTE_MOVED"
    assert (info.reason, dict(info.metadata)) == ("QUOTE_MOVED", {"quote": "7"})


# --- bugs: fatal, and retryable under a status the handler picks ---


def test_a_bug_is_unhandled_handler_error_with_its_key_and_args_as_cause():
    err = Bug("LEDGER_INVARIANT_BROKEN", balance=-1)

    assert isinstance(err, CodedError)
    assert err.code == "UNHANDLED_HANDLER_ERROR"
    assert err.grpc == GrpcCode.INTERNAL
    assert err.error_class is ErrorClass.BugFatal
    assert err.extras == {
        "cause": "LEDGER_INVARIANT_BROKEN",
        "cause.balance": "-1",
    }
    assert str(err) == (
        "UNHANDLED_HANDLER_ERROR cause=LEDGER_INVARIANT_BROKEN cause.balance=-1"
    )


def test_a_bug_crosses_as_internal_unhandled_handler_error_with_its_cause():
    data, ret = _error_status(Bug("LEDGER_INVARIANT_BROKEN", balance=-1))
    status, info = _parse(data)

    assert ret == -13
    assert status.code == 13
    assert status.message == "UNHANDLED_HANDLER_ERROR"
    assert info.reason == "UNHANDLED_HANDLER_ERROR"
    assert dict(info.metadata) == {
        "cause": "LEDGER_INVARIANT_BROKEN",
        "cause.balance": "-1",
    }


@pytest.mark.parametrize(
    "status",
    [GrpcCode.UNAVAILABLE, GrpcCode.DEADLINE_EXCEEDED, GrpcCode.RESOURCE_EXHAUSTED],
)
def test_a_retryable_bug_goes_out_under_the_status_its_handler_picks(status):
    # angzarr-router's CodedError::retryable_bug(RetryableStatus, key, args).
    err = RetryableBug("LEDGER_UNREACHABLE", status, host="db-1")

    assert isinstance(err, CodedError)
    assert err.code == "UNHANDLED_HANDLER_ERROR"
    assert err.grpc == status
    assert err.error_class is ErrorClass.BugRetryable
    assert err.extras == {"cause": "LEDGER_UNREACHABLE", "cause.host": "db-1"}
    data, ret = _error_status(err)
    wire, info = _parse(data)
    assert (wire.code, ret) == (int(status), -int(status))
    assert wire.message == "UNHANDLED_HANDLER_ERROR"
    assert info.reason == "UNHANDLED_HANDLER_ERROR"


def test_the_retryable_statuses_are_their_grpc_numbers():
    assert (
        GrpcCode.DEADLINE_EXCEEDED,
        GrpcCode.RESOURCE_EXHAUSTED,
        GrpcCode.UNAVAILABLE,
    ) == (4, 8, 14)


@pytest.mark.parametrize(
    "status",
    [
        GrpcCode.INTERNAL,
        GrpcCode.UNKNOWN,
        GrpcCode.ABORTED,
        GrpcCode.UNIMPLEMENTED,
        GrpcCode.FAILED_PRECONDITION,
        0,
    ],
)
def test_a_retryable_bug_outside_the_retryable_statuses_is_refused(status):
    with pytest.raises(ValueError, match="retryable"):
        RetryableBug("LEDGER_UNREACHABLE", status)


def test_an_arg_named_status_is_an_arg_not_the_retryable_bugs_status():
    err = RetryableBug("LEDGER_UNREACHABLE", GrpcCode.UNAVAILABLE, status="down")

    assert err.grpc == GrpcCode.UNAVAILABLE
    assert err.extras == {"cause": "LEDGER_UNREACHABLE", "cause.status": "down"}


def test_a_handler_fails_with_a_rejection_an_immediate_return_or_a_bug_only():
    assert not hasattr(router_module, "reject")
    assert "reject" not in router_module.__all__
    assert {"Rejection", "Immediate", "Bug", "RetryableBug", "ErrorClass"} <= set(
        router_module.__all__
    )


# --- the four classes, decided by the status alone ---


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (GrpcCode.FAILED_PRECONDITION, "BusinessFatal"),
        (GrpcCode.INVALID_ARGUMENT, "BusinessFatal"),
        (GrpcCode.NOT_FOUND, "BusinessFatal"),
        (GrpcCode.ALREADY_EXISTS, "BusinessFatal"),
        (GrpcCode.OUT_OF_RANGE, "BusinessFatal"),
        (GrpcCode.PERMISSION_DENIED, "BusinessFatal"),
        (GrpcCode.ABORTED, "BusinessImmediate"),
        (GrpcCode.INTERNAL, "BugFatal"),
        (GrpcCode.UNKNOWN, "BugFatal"),
        (GrpcCode.DATA_LOSS, "BugFatal"),
        (GrpcCode.UNAVAILABLE, "BugRetryable"),
        (GrpcCode.DEADLINE_EXCEEDED, "BugRetryable"),
        (GrpcCode.RESOURCE_EXHAUSTED, "BugRetryable"),
        (GrpcCode.UNIMPLEMENTED, "BugRetryable"),
        (1, "BugRetryable"),  # CANCELLED: one of "the rest"
        (16, "BugRetryable"),  # UNAUTHENTICATED: one of "the rest"
    ],
)
def test_the_status_decides_the_class(status, expected):
    # Mirrors angzarr-router's GrpcCode::class() (decision 14.37, the
    # owner's rulings 2026-10-29, latest).
    assert error_class(status) is ErrorClass[expected]
    assert CodedError("ANY", status).error_class is ErrorClass[expected]


def test_any_decode_error_is_an_internal_bug_naming_the_message_type():
    # ANY_DECODE_FAILED (message_type): malformed input is a bug, and the
    # protobuf runtime's own text is third-party text, carried as
    # ext_err_txt (decision 14.37).
    err = any_decode_error("/test.counter.IncreaseBy", ValueError("truncated"))

    assert err.code == "ANY_DECODE_FAILED"
    assert err.grpc == GrpcCode.INTERNAL
    assert err.extras == {
        "message_type": "test.counter.IncreaseBy",
        "ext_err_txt": "truncated",
    }


def test_fact_record_flags_are_a_tuple():
    record = FactRecord("fact", ["first", "second"])

    assert record.flags == ("first", "second")


# --- CodedError -> Status bytes ---


def test_a_coded_error_maps_to_its_grpc_code_and_error_info():
    data, ret = _error_status(
        CodedError("STOCK_MISSING", GrpcCode.NOT_FOUND, {"sku": "a-1"})
    )

    status, info = _parse(data)
    assert ret == -5
    assert status.code == 5
    assert status.message == "STOCK_MISSING"
    assert status.details[0].type_url == ("type.googleapis.com/google.rpc.ErrorInfo")
    assert info.reason == "STOCK_MISSING"
    assert info.domain == "angzarr.io"
    assert dict(info.metadata) == {"sku": "a-1"}


def test_a_coded_error_without_a_grpc_code_maps_to_invalid_argument():
    data, ret = _error_status(CodedError("BAD", grpc=0))

    status, info = _parse(data)
    assert ret == -3
    assert status.code == 3
    assert status.message == "BAD"
    assert info.reason == "BAD"
    assert dict(info.metadata) == {}


def test_any_other_exception_is_an_unhandled_error_caused_by_a_panic(caplog):
    # Decision 14.37: what escaped a handler stays in the local log; the
    # wire carries UNHANDLED_HANDLER_ERROR with cause HANDLER_PANICKED.
    with caplog.at_level(logging.ERROR):
        data, ret = _error_status(RuntimeError("hard failure"))

    status, info = _parse(data)
    assert ret == -13
    assert status.code == 13
    assert status.message == "UNHANDLED_HANDLER_ERROR"
    assert info.reason == "UNHANDLED_HANDLER_ERROR"
    assert info.domain == "angzarr.io"
    assert dict(info.metadata) == {"cause": "HANDLER_PANICKED"}
    assert b"hard failure" not in data
    assert "hard failure" in caplog.text


# --- Status bytes -> CodedError ---


def test_absent_status_bytes_decode_to_the_return_code_alone():
    err = _decode_status(None, -5)

    assert err.code == ""
    assert err.grpc == 5
    assert err.extras == {}


def test_undecodable_status_bytes_decode_to_the_return_code_alone():
    err = _decode_status(b"\xff", -9)

    assert err.code == ""
    assert err.grpc == 9


def test_status_bytes_decode_to_their_code_reason_and_metadata_not_their_text():
    err = _decode_status(
        _status_with_info(5, "no stock", "STOCK_MISSING", {"sku": "a-1"}), -13
    )

    assert err.code == "STOCK_MISSING"
    assert err.grpc == 5
    assert err.extras == {"sku": "a-1"}
    assert "no stock" not in str(err)


def test_a_status_with_no_code_falls_back_to_the_return_code():
    err = _decode_status(_status_with_info(0, "LOST", "LOST"), -13)

    assert err.grpc == 13
    assert err.code == "LOST"


def test_a_status_without_error_info_takes_its_message_as_its_key():
    # The status message IS the reason key (decision 14.37).
    data = status_pb2.Status(code=9, message="SEAT_TAKEN").SerializeToString()

    err = _decode_status(data, -13)

    assert err.code == "SEAT_TAKEN"
    assert err.grpc == 9
    assert err.extras == {}


def test_a_status_round_trips_through_the_error_model():
    original = CodedError("STOCK_MISSING", GrpcCode.NOT_FOUND, {"k": "v"})

    data, ret = _error_status(original)
    err = _decode_status(data, ret)

    assert (err.code, err.grpc, err.extras) == ("STOCK_MISSING", 5, {"k": "v"})
