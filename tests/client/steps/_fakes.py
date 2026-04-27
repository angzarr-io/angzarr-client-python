"""Reusable fakes for cucumber step files that exercise real production
clients without opening sockets.

Two pieces:

  - :class:`RecordingStub` — a duck-typed stand-in for any of the gRPC
    service stubs (`CommandHandlerCoordinatorServiceStub`,
    `EventQueryServiceStub`, etc.). Records every method call and
    returns canned responses (or raises canned errors) keyed by method
    name. Plug into the production wrapping clients via the new
    `from_stub` / `from_stubs` factories on
    `CommandHandlerClient`, `QueryClient`, `SpeculativeClient`.

  - :class:`StubRpcError` — a minimal `grpc.RpcError`-quacked exception
    that the wrapping clients can catch and wrap in the production
    `GRPCError` class. Code/details are required so error-classification
    assertions work end-to-end.

PARITY_AUDIT.md plan item P1.12.e / finding #24.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, Union

import grpc


class StubRpcError(grpc.RpcError):
    """Minimal `grpc.RpcError` carrying a code() and details().

    The production code paths (`QueryClient.get_event_book`,
    `CommandHandlerClient.handle_command`, etc.) catch
    `grpc.RpcError` and wrap it in `GRPCError(e)`. So this stand-in
    must inherit from `grpc.RpcError` and implement the same surface
    (`code()` and `details()`) as the real exception.
    """

    def __init__(self, code: grpc.StatusCode, details: str) -> None:
        super().__init__(details)
        self._code = code
        self._details = details

    def code(self) -> grpc.StatusCode:
        return self._code

    def details(self) -> str:
        return self._details

    def __str__(self) -> str:
        return self._details


# A canned response can be either a static value or a callable
# (request, **kwargs) -> response, for cases where the response depends
# on what was sent.
CannedResponse = Union[Any, Callable[..., Any]]


class RecordingStub:
    """Duck-typed gRPC stub. Records every call; returns canned values.

    Usage::

        stub = RecordingStub()
        stub.responses["GetEventBook"] = EventBook(...)
        stub.errors["HandleCommand"] = StubRpcError(
            grpc.StatusCode.FAILED_PRECONDITION, "stale sequence"
        )
        client = QueryClient.from_stub(stub)
        client.get_event_book(query)
        # → stub.calls == [("GetEventBook", query, {"timeout": None})]

    Methods not pre-registered return ``None`` (the wrapping client
    will then surface a TypeError; explicitly register every method
    you intend to call).
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any, dict[str, Any]]] = []
        self.responses: dict[str, CannedResponse] = {}
        self.errors: dict[str, BaseException] = {}

    def __getattr__(self, name: str) -> Callable[..., Any]:
        # __getattr__ fires only for unset attributes; so once a name
        # is queried, generate a callable that records + dispatches.
        # No need to cache: each access through gRPC stubs is a
        # one-shot method-name lookup.
        def _caller(request: Any = None, /, **kwargs: Any) -> Any:
            self.calls.append((name, request, dict(kwargs)))
            if name in self.errors:
                raise self.errors[name]
            r = self.responses.get(name)
            if callable(r):
                return r(request, **kwargs)
            return r

        return _caller

    def last_call(self, method: str) -> Optional[tuple[Any, dict[str, Any]]]:
        """Return the (request, kwargs) of the last call to ``method``,
        or ``None`` if it was never called."""
        for n, req, kwargs in reversed(self.calls):
            if n == method:
                return (req, kwargs)
        return None

    def call_count(self, method: str) -> int:
        """How many times ``method`` was invoked."""
        return sum(1 for n, _, _ in self.calls if n == method)
