"""Tests for error types."""

import grpc
from google.protobuf import any_pb2
from google.rpc import error_details_pb2, status_pb2

from angzarr_client.errors import (
    ClientError,
    ConnectionError,
    GRPCError,
    InvalidArgumentError,
    InvalidTimestampError,
    TransportError,
)


class TestClientError:
    """Tests for the ClientError base class: a code and details, never text
    (decision 14.37)."""

    def test_code_and_details(self) -> None:
        err = ClientError("DOMAIN_CONTAINS_COLON", details={"domain": "a:b"})
        assert err.code == "DOMAIN_CONTAINS_COLON"
        assert err.details == {"domain": "a:b"}
        assert err.cause is None

    def test_its_text_is_its_code_then_its_details(self) -> None:
        err = ClientError("DOMAIN_CONTAINS_COLON", details={"domain": "a b"})
        assert str(err) == 'DOMAIN_CONTAINS_COLON domain="a b"'
        assert err.args == ('DOMAIN_CONTAINS_COLON domain="a b"',)

    def test_its_message_is_rendered_from_the_catalog(self) -> None:
        err = ClientError("DOMAIN_CONTAINS_COLON", details={"domain": "a:b"})
        assert err.message == "domain \"a:b\" must not contain ':'"

    def test_a_framework_keys_message_is_the_specs(self) -> None:
        err = ClientError("HOST_REASON_HANDSHAKE_MALFORMED", details={"line": "x"})
        assert err.message == (
            'malformed handshake "x": expected angzarr-handshake v1 unix:<absolute path>'
        )

    def test_with_cause(self) -> None:
        """The cause is kept on ``.cause``, never in the text."""
        cause = ValueError("underlying issue")
        err = ClientError("TRANSPORT_ERROR", cause)
        assert err.cause is cause
        assert str(err) == "TRANSPORT_ERROR"

    def test_exception_inheritance(self) -> None:
        assert isinstance(ClientError("X"), Exception)


class TestConnectionError:
    """Tests for ConnectionError."""

    def test_defaults(self) -> None:
        err = ConnectionError()
        assert err.code == "CONNECTION_FAILED"
        assert str(err) == "CONNECTION_FAILED"
        assert err.message == "connection failed"
        assert err.cause is None

    def test_exception_inheritance(self) -> None:
        err = ConnectionError()
        assert isinstance(err, ClientError)
        assert isinstance(err, Exception)


class TestTransportError:
    """Tests for TransportError."""

    def test_wraps_cause(self) -> None:
        cause = OSError("socket error")
        err = TransportError(cause)
        assert str(err) == "TRANSPORT_ERROR"
        assert err.message == "transport error"
        assert err.cause is cause

    def test_exception_inheritance(self) -> None:
        assert isinstance(TransportError(OSError("test")), ClientError)

    def test_uses_inventory_constants(self) -> None:
        from angzarr_client.error_codes import codes

        assert TransportError(OSError("x")).code == codes.TRANSPORT_ERROR


class MockRpcError(grpc.RpcError):
    """Mock RpcError for testing.

    grpc.RpcError itself doesn't have code/details methods - those come
    from grpc.Call. Real gRPC errors inherit from both.
    """

    def __init__(self, code: grpc.StatusCode, details: str = "", trailer: tuple = ()):
        super().__init__()
        self._code = code
        self._details = details
        self._trailer = trailer

    def code(self) -> grpc.StatusCode:
        return self._code

    def details(self) -> str:
        return self._details

    def trailing_metadata(self) -> tuple:
        return self._trailer


def _status_trailer(status: status_pb2.Status) -> tuple:
    return (("grpc-status-details-bin", status.SerializeToString()),)


def _info(reason: str, metadata: dict) -> any_pb2.Any:
    packed = any_pb2.Any()
    packed.Pack(
        error_details_pb2.ErrorInfo(
            reason=reason, domain="angzarr.io", metadata=metadata
        )
    )
    return packed


class TestGRPCError:
    """Tests for GRPCError."""

    def _mock_rpc_error(self, code: grpc.StatusCode, details: str) -> grpc.RpcError:
        """Create a mock RpcError with code() and details() methods."""
        return MockRpcError(code, details)

    def test_wraps_rpc_error(self) -> None:
        """GRPCError wraps an RpcError."""
        rpc_error = self._mock_rpc_error(grpc.StatusCode.INTERNAL, "server error")
        err = GRPCError(rpc_error)
        assert str(err) == "GRPC_ERROR"
        assert err.message == "grpc error"
        assert err.cause is rpc_error

    def test_code_property(self) -> None:
        """Audit #59: ``.grpc_code`` is the upstream gRPC StatusCode.
        ``.code`` is now the SCREAMING_SNAKE structural identifier."""
        from angzarr_client.error_codes import codes

        rpc_error = self._mock_rpc_error(grpc.StatusCode.NOT_FOUND, "not found")
        err = GRPCError(rpc_error)
        assert err.grpc_code == grpc.StatusCode.NOT_FOUND
        assert err.code == codes.GRPC_ERROR

    def test_details_property(self) -> None:
        """Audit #59: ``.grpc_details`` is the upstream gRPC details
        string; ``.details`` is the structured-detail dict."""
        rpc_error = self._mock_rpc_error(
            grpc.StatusCode.INVALID_ARGUMENT, "bad request"
        )
        err = GRPCError(rpc_error)
        assert err.grpc_details == "bad request"

    def test_is_not_found_true(self) -> None:
        """is_not_found returns True for NOT_FOUND."""
        rpc_error = self._mock_rpc_error(grpc.StatusCode.NOT_FOUND, "")
        err = GRPCError(rpc_error)
        assert err.is_not_found() is True

    def test_is_not_found_false(self) -> None:
        """is_not_found returns False for other codes."""
        rpc_error = self._mock_rpc_error(grpc.StatusCode.INTERNAL, "")
        err = GRPCError(rpc_error)
        assert err.is_not_found() is False

    def test_is_precondition_failed_true(self) -> None:
        """is_precondition_failed returns True for FAILED_PRECONDITION."""
        rpc_error = self._mock_rpc_error(grpc.StatusCode.FAILED_PRECONDITION, "")
        err = GRPCError(rpc_error)
        assert err.is_precondition_failed() is True

    def test_is_precondition_failed_false(self) -> None:
        """is_precondition_failed returns False for other codes."""
        rpc_error = self._mock_rpc_error(grpc.StatusCode.OK, "")
        err = GRPCError(rpc_error)
        assert err.is_precondition_failed() is False

    def test_is_invalid_argument_true(self) -> None:
        """is_invalid_argument returns True for INVALID_ARGUMENT."""
        rpc_error = self._mock_rpc_error(grpc.StatusCode.INVALID_ARGUMENT, "")
        err = GRPCError(rpc_error)
        assert err.is_invalid_argument() is True

    def test_is_invalid_argument_false(self) -> None:
        """is_invalid_argument returns False for other codes."""
        rpc_error = self._mock_rpc_error(grpc.StatusCode.CANCELLED, "")
        err = GRPCError(rpc_error)
        assert err.is_invalid_argument() is False

    def test_exception_inheritance(self) -> None:
        """GRPCError is a ClientError."""
        rpc_error = self._mock_rpc_error(grpc.StatusCode.OK, "")
        err = GRPCError(rpc_error)
        assert isinstance(err, ClientError)


class TestInvalidArgumentError:
    """Tests for InvalidArgumentError."""

    def test_code_and_text(self) -> None:
        err = InvalidArgumentError("COMMAND_PAYLOAD_MISSING")
        assert str(err) == "COMMAND_PAYLOAD_MISSING"
        assert err.message == "command payload not set"
        assert err.cause is None

    def test_exception_inheritance(self) -> None:
        assert isinstance(InvalidArgumentError("X"), ClientError)


class TestClientErrorBasePredicates:
    """Default predicates on ClientError all return False."""

    def test_base_is_not_found_false(self) -> None:
        assert ClientError("x").is_not_found() is False

    def test_base_is_precondition_failed_false(self) -> None:
        assert ClientError("x").is_precondition_failed() is False

    def test_base_is_invalid_argument_false(self) -> None:
        assert ClientError("x").is_invalid_argument() is False

    def test_base_is_connection_error_false(self) -> None:
        assert ClientError("x").is_connection_error() is False

    def test_invalid_timestamp_inherits_base_predicates(self) -> None:
        err = InvalidTimestampError()
        assert err.is_not_found() is False
        assert err.is_precondition_failed() is False
        assert err.is_invalid_argument() is False
        assert err.is_connection_error() is False


class TestConnectionPredicates:
    def test_connection_error_is_connection_error(self) -> None:
        assert ConnectionError().is_connection_error() is True

    def test_transport_error_is_connection_error(self) -> None:
        assert TransportError(OSError("x")).is_connection_error() is True

    def test_invalid_argument_is_invalid_argument(self) -> None:
        assert InvalidArgumentError("x").is_invalid_argument() is True


class TestGRPCErrorExtraBranches:
    def _err(self, code: grpc.StatusCode) -> GRPCError:
        return GRPCError(MockRpcError(code, ""))

    def test_status_without_a_trailer_is_the_code_and_message(self) -> None:
        rpc_error = MockRpcError(grpc.StatusCode.NOT_FOUND, "SOME_KEY")
        err = GRPCError(rpc_error)
        assert err.status() == status_pb2.Status(code=5, message="SOME_KEY")
        assert err.cause is rpc_error

    def test_a_status_without_trailing_metadata_is_the_code_and_message(
        self,
    ) -> None:
        class Bare:
            def code(self) -> grpc.StatusCode:
                return grpc.StatusCode.INTERNAL

            def details(self) -> None:
                return None

        assert GRPCError(Bare()).status() == status_pb2.Status(code=13)

    def test_code_and_details_are_the_servers_reason_key_and_args(self) -> None:
        """Decision 14.37: the ErrorInfo carries the key and the args."""
        status = status_pb2.Status(
            code=7,
            message="ACCESS_DENIED",
            details=[_info("ACCESS_DENIED", {"a": "b c"})],
        )
        rpc_error = MockRpcError(
            grpc.StatusCode.PERMISSION_DENIED, "ACCESS_DENIED", _status_trailer(status)
        )
        err = GRPCError(rpc_error)
        assert err.code == "ACCESS_DENIED"
        assert err.details == {"a": "b c"}
        assert str(err) == 'ACCESS_DENIED a="b c"'
        assert err.status() == status
        assert err.grpc_code == grpc.StatusCode.PERMISSION_DENIED

    def test_the_first_error_info_names_the_error(self) -> None:
        other = any_pb2.Any()
        other.Pack(error_details_pb2.RetryInfo())
        status = status_pb2.Status(
            code=13, details=[other, _info("FIRST", {}), _info("SECOND", {})]
        )
        err = GRPCError(
            MockRpcError(grpc.StatusCode.INTERNAL, "", _status_trailer(status))
        )
        assert err.code == "FIRST"

    def test_a_status_with_no_error_info_is_grpc_error(self) -> None:
        from angzarr_client.error_codes import codes

        status = status_pb2.Status(code=13, message="SOME_KEY")
        trailer = (("other", b"x"), *_status_trailer(status))
        err = GRPCError(MockRpcError(grpc.StatusCode.INTERNAL, "x", trailer))
        assert err.code == codes.GRPC_ERROR
        assert err.details == {}
        assert err.status() == status

    def test_an_undecodable_trailer_is_the_code_and_message(self) -> None:
        trailer = (("grpc-status-details-bin", b"\xff\xff"),)
        err = GRPCError(MockRpcError(grpc.StatusCode.INTERNAL, "K", trailer))
        assert err.status() == status_pb2.Status(code=13, message="K")

    def test_a_given_code_and_details_win(self) -> None:
        status = status_pb2.Status(code=13, details=[_info("SERVER", {"a": "1"})])
        err = GRPCError(
            MockRpcError(grpc.StatusCode.INTERNAL, "", _status_trailer(status)),
            code="MINE",
            details={"b": "2"},
        )
        assert (err.code, err.details) == ("MINE", {"b": "2"})

    def test_is_connection_error_true_for_unavailable(self) -> None:
        assert self._err(grpc.StatusCode.UNAVAILABLE).is_connection_error() is True

    def test_is_connection_error_false_otherwise(self) -> None:
        assert self._err(grpc.StatusCode.INTERNAL).is_connection_error() is False


class TestCommandRejectedErrorPredicates:
    def test_precondition_failed_constructor(self) -> None:
        """Audit #59: factories take ``(code, details)``, never text."""
        from angzarr_client.errors import CommandRejectedError

        err = CommandRejectedError.precondition_failed("BAD_STATE")
        assert err.code == "BAD_STATE"
        assert str(err) == "BAD_STATE"
        assert err.status_code == "FAILED_PRECONDITION"
        assert err.is_precondition_failed() is True
        assert err.is_invalid_argument() is False
        assert err.is_not_found() is False

    def test_invalid_argument_constructor(self) -> None:
        from angzarr_client.errors import CommandRejectedError

        err = CommandRejectedError.invalid_argument("BAD_INPUT")
        assert err.code == "BAD_INPUT"
        assert err.status_code == "INVALID_ARGUMENT"
        assert err.is_invalid_argument() is True
        assert err.is_precondition_failed() is False

    def test_not_found_constructor(self) -> None:
        from angzarr_client.errors import CommandRejectedError

        err = CommandRejectedError.not_found("NO_SUCH_AGGREGATE")
        assert err.code == "NO_SUCH_AGGREGATE"
        assert err.status_code == "NOT_FOUND"
        assert err.is_not_found() is True
        assert err.is_precondition_failed() is False
        assert err.is_invalid_argument() is False

    def test_is_a_client_error(self) -> None:
        """CommandRejectedError IS-A ClientError so callers can catch the
        base class and still get rejection-specific predicates without
        casting. P2.3 cross-language taxonomy contract."""
        from angzarr_client.errors import CommandRejectedError

        err = CommandRejectedError.not_found("MISSING")
        assert isinstance(err, ClientError)
        as_base: ClientError = err
        assert as_base.is_not_found() is True
        assert as_base.is_precondition_failed() is False

    def test_details_carry_structured_context(self) -> None:
        """``details`` is the structured context, and the message's args."""
        from angzarr_client.errors import CommandRejectedError

        err = CommandRejectedError.invalid_argument(
            "VALUE_NOT_POSITIVE", {"field": "amount"}
        )
        assert err.details["field"] == "amount"
        assert err.message == "amount must be positive"
        assert str(err) == "VALUE_NOT_POSITIVE field=amount"


class TestInvalidTimestampError:
    """Tests for InvalidTimestampError."""

    def test_defaults(self) -> None:
        err = InvalidTimestampError()
        assert err.code == "TIMESTAMP_PARSE_FAILED"
        assert err.cause is None

    def test_carries_the_parsers_text_as_an_arg(self) -> None:
        err = InvalidTimestampError(details={"ext_err_txt": "bad"})
        assert str(err) == "TIMESTAMP_PARSE_FAILED ext_err_txt=bad"
        assert err.message == "the timestamp is not RFC 3339 (bad)"

    def test_exception_inheritance(self) -> None:
        assert isinstance(InvalidTimestampError(), ClientError)


class TestConstructorDefaultsAndPassThrough:
    """Default code per error type, and that ``code`` / ``details`` given to
    a subclass reach the base ``ClientError`` fields (details are
    stringified)."""

    def test_client_error_defaults(self) -> None:
        err = ClientError("BOOM")
        assert err.code == "BOOM"
        assert err.details == {}
        assert err.args == ("BOOM",)

    def test_a_code_no_catalog_holds_renders_as_its_text(self) -> None:
        assert ClientError("BOOM", details={"n": 1}).message == "BOOM n=1"

    def test_connection_error_passes_code_and_details(self) -> None:
        err = ConnectionError("DIAL_REFUSED", details={"port": 1310})
        assert err.code == "DIAL_REFUSED"
        assert err.details == {"port": "1310"}

    def test_transport_error_passes_details(self) -> None:
        err = TransportError(OSError("x"), details={"attempt": 3})
        assert err.details == {"attempt": "3"}

    def test_grpc_error_passes_details(self) -> None:
        err = GRPCError(
            MockRpcError(grpc.StatusCode.INTERNAL), details={"method": "Handle"}
        )
        assert err.details == {"method": "Handle"}

    def test_invalid_argument_error_passes_details(self) -> None:
        err = InvalidArgumentError("PROTO_UUID_INVALID", details={"field": "root"})
        assert err.details == {"field": "root"}
        assert err.message == "root is not a UUID"

    def test_invalid_timestamp_error_passes_code_and_details(self) -> None:
        err = InvalidTimestampError("TIMESTAMP_OUT_OF_RANGE", details={"value": "9999"})
        assert err.code == "TIMESTAMP_OUT_OF_RANGE"
        assert err.details == {"value": "9999"}

    def test_command_rejected_defaults(self) -> None:
        from angzarr_client.errors import CommandRejectedError

        err = CommandRejectedError("GUARD_FAILED")
        assert err.status_code == "FAILED_PRECONDITION"
        assert err.is_precondition_failed() is True
        assert err.code == "GUARD_FAILED"
        assert err.cover is None

    def test_command_rejected_keeps_cover(self) -> None:
        from angzarr_client.errors import CommandRejectedError

        cover = object()
        err = CommandRejectedError("GUARD_FAILED", cover=cover)
        assert err.cover is cover
