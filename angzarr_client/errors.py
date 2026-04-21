"""Error types for the Angzarr client library."""

import grpc


class ClientError(Exception):
    """Base class for client errors.

    Predicate methods (``is_not_found``, ``is_precondition_failed``,
    ``is_invalid_argument``, ``is_connection_error``) return ``False`` here and
    are overridden by subclasses. This lets callers write
    ``if err.is_not_found(): ...`` without casting. Mirrors the pattern in the
    Java and C# clients.
    """

    def __init__(self, message: str, cause: Exception | None = None):
        super().__init__(message)
        self.message = message
        self.cause = cause

    def __str__(self) -> str:
        if self.cause:
            return f"{self.message}: {self.cause}"
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

    def __init__(self, message: str):
        super().__init__(f"connection failed: {message}")

    def is_connection_error(self) -> bool:
        return True


class TransportError(ClientError):
    """Transport-level error."""

    def __init__(self, cause: Exception):
        super().__init__("transport error", cause)

    def is_connection_error(self) -> bool:
        return True


class GRPCError(ClientError):
    """gRPC error from the server."""

    def __init__(self, cause: grpc.RpcError):
        super().__init__("grpc error", cause)
        self._rpc_error = cause

    @property
    def code(self) -> grpc.StatusCode:
        """Return the gRPC status code."""
        return self._rpc_error.code()

    @property
    def details(self) -> str:
        """Return the error details."""
        return self._rpc_error.details()

    def status(self) -> grpc.RpcError:
        """Return the underlying gRPC RpcError (status)."""
        return self._rpc_error

    def is_not_found(self) -> bool:
        """Return True if this is a NOT_FOUND error."""
        return self.code == grpc.StatusCode.NOT_FOUND

    def is_precondition_failed(self) -> bool:
        """Return True if this is a FAILED_PRECONDITION error."""
        return self.code == grpc.StatusCode.FAILED_PRECONDITION

    def is_invalid_argument(self) -> bool:
        """Return True if this is an INVALID_ARGUMENT error."""
        return self.code == grpc.StatusCode.INVALID_ARGUMENT

    def is_connection_error(self) -> bool:
        """Return True for UNAVAILABLE status."""
        return self.code == grpc.StatusCode.UNAVAILABLE


class InvalidArgumentError(ClientError):
    """Invalid argument provided by caller."""

    def __init__(self, message: str):
        super().__init__(f"invalid argument: {message}")

    def is_invalid_argument(self) -> bool:
        return True


class InvalidTimestampError(ClientError):
    """Failed to parse timestamp."""

    def __init__(self, message: str):
        super().__init__(f"invalid timestamp: {message}")


class CommandRejectedError(ClientError):
    """Command was rejected due to business rule violation.

    Status codes and retry semantics:
      - FAILED_PRECONDITION: state-based rejection; retryable after refreshing state.
      - INVALID_ARGUMENT: bad input; not retryable.
      - NOT_FOUND: aggregate does not exist; not retryable — refetching won't help.
    """

    def __init__(self, message: str, status_code: str = "FAILED_PRECONDITION"):
        super().__init__(message)
        self.status_code = status_code

    @staticmethod
    def precondition_failed(message: str) -> "CommandRejectedError":
        """Create a FAILED_PRECONDITION error for guard failures."""
        return CommandRejectedError(message, "FAILED_PRECONDITION")

    @staticmethod
    def invalid_argument(message: str) -> "CommandRejectedError":
        """Create an INVALID_ARGUMENT error for input validation failures."""
        return CommandRejectedError(message, "INVALID_ARGUMENT")

    @staticmethod
    def not_found(message: str) -> "CommandRejectedError":
        """Create a NOT_FOUND error for missing-aggregate failures."""
        return CommandRejectedError(message, "NOT_FOUND")

    def is_precondition_failed(self) -> bool:
        return self.status_code == "FAILED_PRECONDITION"

    def is_invalid_argument(self) -> bool:
        return self.status_code == "INVALID_ARGUMENT"

    def is_not_found(self) -> bool:
        return self.status_code == "NOT_FOUND"
