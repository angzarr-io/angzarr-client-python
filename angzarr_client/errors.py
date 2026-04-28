"""Error types for the Angzarr client library.

Audit finding #59 (structural error model). Every error carries:

- A static ``message`` — the same exact string for the same predicate
  failure across all call sites, suitable for log greppability,
  cross-language equality checks, and i18n keys.
- A stable ``code`` identifier (``SCREAMING_SNAKE``) — programmatic
  dispatch and cucumber assertions key off this.
- Structured ``details`` — runtime context (field name, type URL,
  domain, status code) that varies per call site.

Callers MUST NOT interpolate runtime values into ``message``; that
defeats the static-string contract. Put runtime values in ``details``.

Mirrors Rust's ``CommandRejectedError`` / ``ClientError`` with the same
``code`` values for cross-language symmetry.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import grpc


class ClientError(Exception):
    """Base class for client errors.

    Predicate methods (``is_not_found``, ``is_precondition_failed``,
    ``is_invalid_argument``, ``is_connection_error``) return ``False`` here and
    are overridden by subclasses. This lets callers write
    ``if err.is_not_found(): ...`` without casting.

    Audit finding #59 fields:
      - ``message`` — static human-readable string
      - ``code`` — SCREAMING_SNAKE stable identifier
      - ``details`` — runtime context as ``dict[str, str]``
    """

    def __init__(
        self,
        message: str,
        cause: Exception | None = None,
        *,
        code: str = "",
        details: Mapping[str, Any] | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.code = code
        self.cause = cause
        self.details: dict[str, str] = {k: str(v) for k, v in (details or {}).items()}

    def __str__(self) -> str:
        # Static message only — no concatenation. Audit #59.
        return self.message

    def is_not_found(self) -> bool:
        """Return True if this is a NOT_FOUND error."""
        return False

    def is_precondition_failed(self) -> bool:
        """Return True if this is a FAILED_PRECONDITION error."""
        return False

    def is_invalid_argument(self) -> bool:
        """Return True if this is an INVALID_ARGUMENT error."""
        return False

    def is_connection_error(self) -> bool:
        """Return True if this is a connection or transport error."""
        return False


class ConnectionError(ClientError):
    """Failed to establish connection to the server."""

    def __init__(
        self,
        message: str = "connection failed",
        *,
        code: str = "CONNECTION_FAILED",
        details: Mapping[str, Any] | None = None,
    ):
        super().__init__(message, code=code, details=details)

    def is_connection_error(self) -> bool:
        return True


class TransportError(ClientError):
    """Transport-level error."""

    def __init__(
        self,
        cause: Exception,
        *,
        code: str = "TRANSPORT_ERROR",
        details: Mapping[str, Any] | None = None,
    ):
        super().__init__("transport error", cause, code=code, details=details)

    def is_connection_error(self) -> bool:
        return True


class GRPCError(ClientError):
    """gRPC error from the server."""

    def __init__(
        self,
        cause: grpc.RpcError,
        *,
        code: str = "GRPC_ERROR",
        details: Mapping[str, Any] | None = None,
    ):
        super().__init__("grpc error", cause, code=code, details=details)
        self._rpc_error = cause

    @property
    def grpc_code(self) -> grpc.StatusCode:
        """Return the gRPC status code."""
        return self._rpc_error.code()

    # Back-compat alias — old name was ``code`` (a method); renamed to
    # ``grpc_code`` so the `.code` attribute can carry the SCREAMING_SNAKE id.
    @property
    def grpc_details(self) -> str:
        """Return the gRPC server-side details string."""
        return self._rpc_error.details()

    def status(self) -> grpc.RpcError:
        """Return the underlying gRPC RpcError (status)."""
        return self._rpc_error

    def is_not_found(self) -> bool:
        return self.grpc_code == grpc.StatusCode.NOT_FOUND

    def is_precondition_failed(self) -> bool:
        return self.grpc_code == grpc.StatusCode.FAILED_PRECONDITION

    def is_invalid_argument(self) -> bool:
        return self.grpc_code == grpc.StatusCode.INVALID_ARGUMENT

    def is_connection_error(self) -> bool:
        """Return True for UNAVAILABLE status."""
        return self.grpc_code == grpc.StatusCode.UNAVAILABLE


class InvalidArgumentError(ClientError):
    """Invalid argument provided by caller."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "INVALID_ARGUMENT",
        details: Mapping[str, Any] | None = None,
    ):
        super().__init__(message, code=code, details=details)

    def is_invalid_argument(self) -> bool:
        return True


class InvalidTimestampError(ClientError):
    """Failed to parse timestamp."""

    def __init__(
        self,
        message: str = "invalid timestamp",
        *,
        code: str = "INVALID_TIMESTAMP",
        details: Mapping[str, Any] | None = None,
    ):
        super().__init__(message, code=code, details=details)


class CommandRejectedError(ClientError):
    """Command was rejected due to business rule violation.

    Status codes and retry semantics:
      - FAILED_PRECONDITION: state-based rejection; retryable after refreshing state.
      - INVALID_ARGUMENT: bad input; not retryable.
      - NOT_FOUND: aggregate does not exist; not retryable — refetching won't help.

    Audit finding #59: callers pass a static ``message``, a SCREAMING_SNAKE
    ``code``, and structured ``details`` kwargs. The factory methods
    (``precondition_failed`` / ``invalid_argument`` / ``not_found``) bind
    the appropriate ``status_code``.
    """

    def __init__(
        self,
        message: str,
        status_code: str = "FAILED_PRECONDITION",
        *,
        code: str = "",
        details: Mapping[str, Any] | None = None,
    ):
        super().__init__(message, code=code, details=details)
        self.status_code = status_code

    @staticmethod
    def precondition_failed(
        code: str,
        message: str,
        details: Mapping[str, Any] | None = None,
    ) -> "CommandRejectedError":
        """Create a FAILED_PRECONDITION error for guard failures.

        Args:
            code: SCREAMING_SNAKE stable identifier (use a constant from
                ``angzarr_client.error_codes.codes``).
            message: Static human-readable message (no interpolation;
                use a constant from ``angzarr_client.error_codes.messages``).
            details: Structured runtime context — keys should be from
                ``angzarr_client.error_codes.keys``.
        """
        return CommandRejectedError(
            message=message,
            status_code="FAILED_PRECONDITION",
            code=code,
            details=details,
        )

    @staticmethod
    def invalid_argument(
        code: str,
        message: str,
        details: Mapping[str, Any] | None = None,
    ) -> "CommandRejectedError":
        """Create an INVALID_ARGUMENT error for input validation failures."""
        return CommandRejectedError(
            message=message,
            status_code="INVALID_ARGUMENT",
            code=code,
            details=details,
        )

    @staticmethod
    def not_found(
        code: str,
        message: str,
        details: Mapping[str, Any] | None = None,
    ) -> "CommandRejectedError":
        """Create a NOT_FOUND error for missing-aggregate failures."""
        return CommandRejectedError(
            message=message,
            status_code="NOT_FOUND",
            code=code,
            details=details,
        )

    def is_precondition_failed(self) -> bool:
        return self.status_code == "FAILED_PRECONDITION"

    def is_invalid_argument(self) -> bool:
        return self.status_code == "INVALID_ARGUMENT"

    def is_not_found(self) -> bool:
        return self.status_code == "NOT_FOUND"
