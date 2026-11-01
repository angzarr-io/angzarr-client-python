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


class GRPCError(ClientError):
    """gRPC error from the server."""

    def __init__(
        self,
        cause: grpc.RpcError,
        *,
        code: str = codes.GRPC_ERROR,
        details: Mapping[str, Any] | None = None,
    ):
        super().__init__(code, cause, details=details)
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
