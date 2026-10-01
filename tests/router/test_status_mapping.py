"""The binding's error model: CodedError construction, the reject /
any_decode_error helpers, and the CodedError <-> google.rpc.Status (with
ErrorInfo) mapping that crosses the FFI in both directions."""

from __future__ import annotations

from google.protobuf import any_pb2
from google.rpc import error_details_pb2, status_pb2

from angzarr_client.router import (
    CodedError,
    FactRecord,
    GrpcCode,
    any_decode_error,
    reject,
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
    assert err.message == ""
    assert err.grpc == GrpcCode.INTERNAL
    assert err.extras == {}
    assert str(err) == ""


def test_coded_error_keeps_a_copy_of_its_extras():
    extras = {"domain": "ledger"}
    err = CodedError("NOT_THERE", "missing", GrpcCode.NOT_FOUND, extras)
    extras["domain"] = "changed"

    assert err.extras == {"domain": "ledger"}
    assert err.grpc == 5


def test_coded_error_text_is_code_then_message_or_message_alone():
    assert str(CodedError("VALUE_NOT_POSITIVE", "must be positive")) == (
        "VALUE_NOT_POSITIVE: must be positive"
    )
    assert str(CodedError(message="must be positive")) == "must be positive"


def test_reject_is_an_invalid_argument_carrying_code_and_message():
    err = reject("VALUE_NOT_POSITIVE", "increase amount must be positive")

    assert err.code == "VALUE_NOT_POSITIVE"
    assert err.message == "increase amount must be positive"
    assert err.grpc == GrpcCode.INVALID_ARGUMENT
    assert err.extras == {}


def test_any_decode_error_names_the_type_url_and_cause():
    err = any_decode_error("/test.counter.IncreaseBy", ValueError("truncated"))

    assert err.code == "ANY_DECODE_FAILED"
    assert err.message == "decode Any '/test.counter.IncreaseBy': truncated"
    assert err.grpc == GrpcCode.INVALID_ARGUMENT
    assert err.extras == {"type_url": "/test.counter.IncreaseBy"}


def test_fact_record_flags_are_a_tuple():
    record = FactRecord("fact", ["first", "second"])

    assert record.flags == ("first", "second")


# --- CodedError -> Status bytes ---


def test_a_coded_error_maps_to_its_grpc_code_and_error_info():
    data, ret = _error_status(
        CodedError("STOCK_MISSING", "no stock", GrpcCode.NOT_FOUND, {"sku": "a-1"})
    )

    status, info = _parse(data)
    assert ret == -5
    assert status.code == 5
    assert status.message == "no stock"
    assert status.details[0].type_url == ("type.googleapis.com/google.rpc.ErrorInfo")
    assert info.reason == "STOCK_MISSING"
    assert info.domain == "angzarr.io"
    assert dict(info.metadata) == {"sku": "a-1"}


def test_a_coded_error_without_a_grpc_code_maps_to_invalid_argument():
    data, ret = _error_status(CodedError("BAD", "bad", grpc=0))

    status, info = _parse(data)
    assert ret == -3
    assert status.code == 3
    assert info.reason == "BAD"
    assert dict(info.metadata) == {}


def test_any_other_exception_maps_to_an_unhandled_internal_failure():
    data, ret = _error_status(RuntimeError("hard failure"))

    status, info = _parse(data)
    assert ret == -13
    assert status.code == 13
    assert status.message == "hard failure"
    assert info.reason == "UNHANDLED_HANDLER_ERROR"
    assert info.domain == "angzarr.io"
    assert dict(info.metadata) == {}


# --- Status bytes -> CodedError ---


def test_absent_status_bytes_decode_to_the_return_code_alone():
    err = _decode_status(None, -5)

    assert err.code == ""
    assert err.message == ""
    assert err.grpc == 5
    assert err.extras == {}


def test_undecodable_status_bytes_decode_to_the_return_code_alone():
    err = _decode_status(b"\xff", -9)

    assert err.code == ""
    assert err.message == ""
    assert err.grpc == 9


def test_status_bytes_decode_to_their_code_message_reason_and_metadata():
    err = _decode_status(
        _status_with_info(5, "no stock", "STOCK_MISSING", {"sku": "a-1"}), -13
    )

    assert err.code == "STOCK_MISSING"
    assert err.message == "no stock"
    assert err.grpc == 5
    assert err.extras == {"sku": "a-1"}


def test_a_status_with_no_code_falls_back_to_the_return_code():
    err = _decode_status(_status_with_info(0, "lost", "LOST"), -13)

    assert err.grpc == 13
    assert err.code == "LOST"


def test_a_status_without_error_info_decodes_with_no_reason():
    data = status_pb2.Status(code=9, message="precondition").SerializeToString()

    err = _decode_status(data, -13)

    assert err.code == ""
    assert err.message == "precondition"
    assert err.grpc == 9
    assert err.extras == {}


def test_a_status_round_trips_through_the_error_model():
    original = CodedError("STOCK_MISSING", "no stock", GrpcCode.NOT_FOUND, {"k": "v"})

    data, ret = _error_status(original)
    err = _decode_status(data, ret)

    assert (err.code, err.message, err.grpc, err.extras) == (
        "STOCK_MISSING",
        "no stock",
        5,
        {"k": "v"},
    )
