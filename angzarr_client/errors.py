"""Error types for the Angzarr client library.

Audit finding #59 (structural error model), decision 14.37 ("no
constructor takes text"). Every error carries:

- a stable ``code`` (``SCREAMING_SNAKE``): a framework reason key, or one of
  this package's own (:mod:`angzarr_client.error_codes.codes`);
- structured ``details``: runtime context (a field name, a type URL, a
  domain) by name.

No error is given English. ``str()`` of an error is its code then its
details (:func:`angzarr_client.diagnostic.display`); its ``message`` is the
English rendered from a catalog keyed by the code
(:func:`angzarr_client.diagnostic.render`): the spec's for a framework
key, this package's own for its codes.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import grpc
from google.protobuf.message import DecodeError
from google.rpc import error_details_pb2, status_pb2

from .diagnostic import display, render
from .error_codes import codes


class ClientError(Exception):
    """Base class for client errors: a ``code`` and its ``details``.

    Predicate methods (``is_not_found``, ``is_precondition_failed``,
    ``is_invalid_argument``, ``is_aborted``, ``is_connection_error``) return
    ``False`` here and
    are overridden by subclasses. This lets callers write
    ``if err.is_not_found(): ...`` without casting.
    """

    def __init__(
        self,
        code: str,
        cause: Exception | None = None,
        *,
        details: Mapping[str, Any] | None = None,
    ):
        self.code = code
        self.cause = cause
        self.details: dict[str, str] = {k: str(v) for k, v in (details or {}).items()}
        super().__init__(display(code, self.details))

    @property
    def message(self) -> str:
        """The error's English, from the catalog keyed by its code."""
        return render(self.code, self.details)

    def is_not_found(self) -> bool:
        """Return True if this is a NOT_FOUND error."""
        return False

    def is_precondition_failed(self) -> bool:
        """Return True if this is a FAILED_PRECONDITION error."""
        return False

    def is_invalid_argument(self) -> bool:
        """Return True if this is an INVALID_ARGUMENT error."""
        return False

    def is_aborted(self) -> bool:
        """Return True if this is an ABORTED error: a stale expected sequence
        (or an unsettled conflict). Retrying means re-reading the stream and
        re-issuing the command."""
        return False

    def is_connection_error(self) -> bool:
        """Return True if this is a connection or transport error."""
        return False


class ConnectionError(ClientError):
    """Failed to establish connection to the server."""

    def __init__(
        self,
        code: str = codes.CONNECTION_FAILED,
        *,
        details: Mapping[str, Any] | None = None,
    ):
        super().__init__(code, details=details)

    def is_connection_error(self) -> bool:
        return True


class TransportError(ClientError):
    """Transport-level error."""

    def __init__(
        self,
        cause: Exception,
        *,
        code: str = codes.TRANSPORT_ERROR,
        details: Mapping[str, Any] | None = None,
    ):
        super().__init__(code, cause, details=details)

    def is_connection_error(self) -> bool:
        return True


# The trailer a gRPC status's details travel in, a serialized
# ``google.rpc.Status``.
_STATUS_DETAILS_KEY = "grpc-status-details-bin"


def _rpc_status(rpc_error: grpc.RpcError) -> status_pb2.Status:
    """The ``google.rpc.Status`` an RPC failed with: the one its
    ``grpc-status-details-bin`` trailer carries, else one holding only its
    status code and message (an error raised outside a call, or by a
    server sending no details)."""
    trailing = getattr(rpc_error, "trailing_metadata", None)
    for key, value in (trailing() if callable(trailing) else None) or ():
        if key == _STATUS_DETAILS_KEY:
            try:
                return status_pb2.Status.FromString(value)
            except DecodeError:
                break
    return status_pb2.Status(
        code=rpc_error.code().value[0], message=rpc_error.details() or ""
    )


def _error_info(status: status_pb2.Status) -> error_details_pb2.ErrorInfo | None:
    """The status's ``google.rpc.ErrorInfo``: its reason key and args."""
    for detail in status.details:
        info = error_details_pb2.ErrorInfo()
        if detail.Unpack(info):
            return info
    return None


class GRPCError(ClientError):
    """gRPC error from the server.

    Its ``code`` and ``details`` are the server's reason key and args, the
    ``ErrorInfo`` reason and metadata of the status's details (decision
    14.37: on the wire the status message is the key, its ``ErrorInfo`` the
    key and the args); a status with no ``ErrorInfo`` is ``GRPC_ERROR`` with
    no args. :meth:`status` is the whole ``google.rpc.Status``.
    """

    def __init__(
        self,
        cause: grpc.RpcError,
        *,
        code: str | None = None,
        details: Mapping[str, Any] | None = None,
    ):
        self._rpc_error = cause
        self._status = _rpc_status(cause)
        info = _error_info(self._status)
        if code is None:
            code = info.reason if info is not None else codes.GRPC_ERROR
        if details is None and info is not None:
            details = info.metadata
        super().__init__(code, cause, details=details)

    @property
    def grpc_code(self) -> grpc.StatusCode:
        """Return the gRPC status code."""
        return self._rpc_error.code()

    @property
    def grpc_details(self) -> str:
        """Return the gRPC server-side details string."""
        return self._rpc_error.details()

    def status(self) -> status_pb2.Status:
        """The full ``google.rpc.Status``: its code, its message (the reason
        key) and its details (the ``ErrorInfo`` first). The RpcError itself
        is :attr:`cause`."""
        return self._status

    def is_not_found(self) -> bool:
        return self.grpc_code == grpc.StatusCode.NOT_FOUND

    def is_precondition_failed(self) -> bool:
        return self.grpc_code == grpc.StatusCode.FAILED_PRECONDITION

    def is_invalid_argument(self) -> bool:
        return self.grpc_code == grpc.StatusCode.INVALID_ARGUMENT

    def is_aborted(self) -> bool:
        return self.grpc_code == grpc.StatusCode.ABORTED

    def is_connection_error(self) -> bool:
        """Return True for UNAVAILABLE status."""
        return self.grpc_code == grpc.StatusCode.UNAVAILABLE


class InvalidArgumentError(ClientError):
    """Invalid argument provided by caller."""

    def __init__(
        self,
        code: str,
        *,
        details: Mapping[str, Any] | None = None,
    ):
        super().__init__(code, details=details)

    def is_invalid_argument(self) -> bool:
        return True


class InvalidTimestampError(ClientError):
    """Failed to parse timestamp."""

    def __init__(
        self,
        code: str = codes.TIMESTAMP_PARSE_FAILED,
        *,
        details: Mapping[str, Any] | None = None,
    ):
        super().__init__(code, details=details)


class CommandRejectedError(ClientError):
    """Command was rejected due to business rule violation.

    Status codes and retry semantics:
      - FAILED_PRECONDITION: state-based rejection; retryable after refreshing state.
      - INVALID_ARGUMENT: bad input; not retryable.
      - NOT_FOUND: aggregate does not exist; not retryable — refetching won't help.

    Audit finding #59, decision 14.37: callers pass a SCREAMING_SNAKE
    ``code`` and structured ``details``, never text. The factory methods
    (``precondition_failed`` / ``invalid_argument`` / ``not_found``) bind
    the appropriate ``status_code``.

    The ``cover`` attribute is the addressing envelope (``domain``, ``root``,
    ``correlation_id``, ``edition``) of the command that produced this
    rejection. Handlers do not populate it; the router stamps it from the
    incoming ``CommandRequest`` at the dispatch boundary so every rejection
    is traceable to its originating workflow without each call site having
    to thread the context.
    """

    def __init__(
        self,
        code: str,
        status_code: str = "FAILED_PRECONDITION",
        *,
        details: Mapping[str, Any] | None = None,
        cover: Any | None = None,
    ):
        super().__init__(code, details=details)
        self.status_code = status_code
        # ``cover`` is an ``angzarr_client.proto.angzarr.types_pb2.Cover``
        # when populated. Typed as ``Any`` here to avoid a circular import
        # of the generated proto module from this base errors module.
        self.cover = cover

    @staticmethod
    def precondition_failed(
        code: str,
        details: Mapping[str, Any] | None = None,
    ) -> CommandRejectedError:
        """Create a FAILED_PRECONDITION error for guard failures.

        Args:
            code: SCREAMING_SNAKE stable identifier (use a constant from
                ``angzarr_client.error_codes.codes``).
            details: Structured runtime context — keys should be from
                ``angzarr_client.error_codes.keys``.
        """
        return CommandRejectedError(code, details=details)

    @staticmethod
    def invalid_argument(
        code: str,
        details: Mapping[str, Any] | None = None,
    ) -> CommandRejectedError:
        """Create an INVALID_ARGUMENT error for input validation failures."""
        return CommandRejectedError(code, "INVALID_ARGUMENT", details=details)

    @staticmethod
    def not_found(
        code: str,
        details: Mapping[str, Any] | None = None,
    ) -> CommandRejectedError:
        """Create a NOT_FOUND error for missing-aggregate failures."""
        return CommandRejectedError(code, "NOT_FOUND", details=details)

    def is_precondition_failed(self) -> bool:
        return self.status_code == "FAILED_PRECONDITION"

    def is_invalid_argument(self) -> bool:
        return self.status_code == "INVALID_ARGUMENT"

    def is_not_found(self) -> bool:
        return self.status_code == "NOT_FOUND"

    def is_aborted(self) -> bool:
        return self.status_code == "ABORTED"
