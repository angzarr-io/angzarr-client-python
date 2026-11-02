"""The router's errors: a key, an argument map and a gRPC status, never text
(decision 14.37).

A handler fails with one of :class:`Rejection`, :class:`Refused`,
:class:`Immediate`, :class:`Bug` or :class:`RetryableBug`, or with the
:class:`UnclassifiedError` it does not classify. Anything else escaping
business code is Python's panic: ``ROUTER_REASON_UNHANDLED_HANDLER_ERROR``
with cause ``ROUTER_REASON_HANDLER_PANICKED`` (decision 14.42, C-0669). What
escaped is logged here, never answered. The router answers a status and a key and never classifies
them: the four classes are the coordinator's (decision 14.42).
"""

from __future__ import annotations

import enum
import logging

from google.protobuf import any_pb2
from google.rpc import error_details_pb2, status_pb2

from ..diagnostic import display
from ..proto.io.angzarr.v1 import reason_args
from ..proto.io.angzarr.v1.reasons_pb2 import RouterReason

_log = logging.getLogger(__name__)

# The reverse-DNS domain of every ErrorInfo the router answers.
ERROR_INFO_DOMAIN = "angzarr.io"

# The router's reason keys (angzarr-project proto/io/angzarr/v1/reasons.proto,
# decision 14.44). A key on the wire is its RouterReason value's full name,
# the prefix included ("ROUTER_REASON_FOLD_FAILED"), never its number.
_key = RouterReason.Name
UNHANDLED_HANDLER_ERROR = _key(RouterReason.ROUTER_REASON_UNHANDLED_HANDLER_ERROR)
HANDLER_PANICKED = _key(RouterReason.ROUTER_REASON_HANDLER_PANICKED)
COMMAND_REFUSED = _key(RouterReason.ROUTER_REASON_COMMAND_REFUSED)
FOLD_FAILED = _key(RouterReason.ROUTER_REASON_FOLD_FAILED)
FOLD_FAILED_EMITTED = _key(RouterReason.ROUTER_REASON_FOLD_FAILED_EMITTED)
PERSISTED_EVENT_CORRUPT = _key(RouterReason.ROUTER_REASON_PERSISTED_EVENT_CORRUPT)
ANY_DECODE_FAILED = _key(RouterReason.ROUTER_REASON_ANY_DECODE_FAILED)
UNKNOWN_COMMAND_TYPE = _key(RouterReason.ROUTER_REASON_UNKNOWN_COMMAND_TYPE)
NO_FACT_HANDLER = _key(RouterReason.ROUTER_REASON_NO_FACT_HANDLER)
NO_REFUSAL_HANDLER = _key(RouterReason.ROUTER_REASON_NO_REFUSAL_HANDLER)
CONFLICT_RESOLUTION_UNSUPPORTED = _key(
    RouterReason.ROUTER_REASON_CONFLICT_RESOLUTION_UNSUPPORTED
)
NO_COMPONENT_OF_KIND = _key(RouterReason.ROUTER_REASON_NO_COMPONENT_OF_KIND)
NO_HANDLERS_REGISTERED = _key(RouterReason.ROUTER_REASON_NO_HANDLERS_REGISTERED)
DUPLICATE_COMPONENT = _key(RouterReason.ROUTER_REASON_DUPLICATE_COMPONENT)


class GrpcCode(enum.IntEnum):
    """The numeric gRPC status codes a coded error travels with."""

    UNKNOWN = 2
    INVALID_ARGUMENT = 3
    DEADLINE_EXCEEDED = 4
    NOT_FOUND = 5
    ALREADY_EXISTS = 6
    PERMISSION_DENIED = 7
    RESOURCE_EXHAUSTED = 8
    FAILED_PRECONDITION = 9
    ABORTED = 10
    OUT_OF_RANGE = 11
    UNIMPLEMENTED = 12
    INTERNAL = 13
    UNAVAILABLE = 14
    DATA_LOSS = 15


# The statuses a business rejection may carry.
_BUSINESS = frozenset(
    {
        GrpcCode.FAILED_PRECONDITION,
        GrpcCode.INVALID_ARGUMENT,
        GrpcCode.NOT_FOUND,
        GrpcCode.ALREADY_EXISTS,
        GrpcCode.OUT_OF_RANGE,
        GrpcCode.PERMISSION_DENIED,
    }
)
# The statuses a handler picks for a bug it marks retryable. Not
# interchangeable: UNAVAILABLE resend, DEADLINE_EXCEEDED resend only if
# idempotent, RESOURCE_EXHAUSTED wait longer.
_RETRYABLE = frozenset(
    {GrpcCode.UNAVAILABLE, GrpcCode.DEADLINE_EXCEEDED, GrpcCode.RESOURCE_EXHAUSTED}
)


def _strings(args: dict[str, object]) -> dict[str, str]:
    return {name: str(value) for name, value in args.items()}


def flatten(name: str, key: str, args: dict[str, str]) -> dict[str, str]:
    """``key`` and ``args`` as a wrapped cause named ``name`` (decision
    14.37): the key under ``name``, each arg under ``<name>.<arg>``."""
    return {name: key, **{f"{name}.{arg}": value for arg, value in args.items()}}


def _check(status: int, allowed: frozenset, what: str) -> int:
    if status not in allowed:
        names = ", ".join(sorted(code.name for code in allowed))
        raise ValueError(f"{what}'s status is one of ({names}), not {status!r}")
    return status


def declared_status(code: str) -> int:
    """The status a key travels with: a spec key's as its
    ``(io.angzarr.v1.reason)`` option declares it (generated into
    ``reason_args.STATUSES``; decision 14.65), INTERNAL for any other code (a
    handler's own, under a bug) and for a readiness key, which never travels
    as a status."""
    return reason_args.STATUSES.get(code) or GrpcCode.INTERNAL


class CodedError(Exception):
    """A key (``code``), a gRPC status (``grpc``) and an argument map
    (``extras``), and for a typed refusal the refusal object (``refusal``,
    an Any). Its text is the code then `` name=value`` per arg; English is
    rendered only where it is shown, from a catalog keyed by the code. The
    status, unless given, is the code's declared one
    (:func:`declared_status`)."""

    def __init__(
        self,
        code: str = "",
        grpc: int | None = None,
        extras: dict[str, str] | None = None,
        refusal: any_pb2.Any | None = None,
    ):
        self.code = code
        self.grpc = int(declared_status(code) if grpc is None else grpc)
        self.extras = dict(extras or {})
        self.refusal = refusal
        super().__init__(display(code, self.extras))

    def status(self) -> status_pb2.Status:
        """The ``google.rpc.Status`` this error travels as: the key is its
        message and its ``ErrorInfo`` reason, the args its metadata, and a
        typed refusal's object follows in the details."""
        info = any_pb2.Any()
        info.Pack(
            error_details_pb2.ErrorInfo(
                reason=self.code, domain=ERROR_INFO_DOMAIN, metadata=self.extras
            )
        )
        details = [info] if self.refusal is None else [info, self.refusal]
        return status_pb2.Status(code=self.grpc, message=self.code, details=details)


class Rejection(CodedError):
    """A business rejection: the command is refused under the handler's key
    and args. ``status``, given positionally, is one of the business set:
    FAILED_PRECONDITION (the default), INVALID_ARGUMENT, NOT_FOUND,
    ALREADY_EXISTS, OUT_OF_RANGE or PERMISSION_DENIED; any other is a
    :class:`ValueError`."""

    def __init__(
        self, key: str, status: int = GrpcCode.FAILED_PRECONDITION, /, **args: object
    ) -> None:
        super().__init__(key, _check(status, _BUSINESS, "a rejection"), _strings(args))


class Refused(CodedError):
    """A typed refusal: one of the command's ``refuses`` messages. It
    travels as ``ROUTER_REASON_COMMAND_REFUSED``, ``refusal_type`` naming the
    object's type, and the object in the status details, under ``status``,
    given positionally: its Reason value's ``refusal_status`` (decision
    14.52), one of the business set, FAILED_PRECONDITION (the default),
    INVALID_ARGUMENT, NOT_FOUND, ALREADY_EXISTS, OUT_OF_RANGE or
    PERMISSION_DENIED; any other is a :class:`ValueError`."""

    def __init__(self, refusal, status: int = GrpcCode.FAILED_PRECONDITION, /) -> None:
        packed = refusal if isinstance(refusal, any_pb2.Any) else pack(refusal)
        refusal_type = type_name(packed.type_url)
        super().__init__(
            COMMAND_REFUSED,
            _check(status, _BUSINESS, "a typed refusal"),
            {reason_args.REFUSAL_TYPE: refusal_type},
            packed,
        )


class Immediate(CodedError):
    """ "Re-read and re-decide": ABORTED under the handler's own key and
    args. Returned at once to the caller; never a policy's OnRefused."""

    def __init__(self, key: str, **args: object) -> None:
        super().__init__(key, GrpcCode.ABORTED, _strings(args))


class Bug(CodedError):
    """A fatal bug: INTERNAL ``ROUTER_REASON_UNHANDLED_HANDLER_ERROR``, the
    handler's key as its ``cause`` and each arg as ``cause.<arg>``."""

    def __init__(self, key: str, **args: object) -> None:
        cause = flatten(reason_args.CAUSE, key, _strings(args))
        super().__init__(UNHANDLED_HANDLER_ERROR, None, cause)


class RetryableBug(CodedError):
    """A retryable bug (a dependency is down, say):
    ``ROUTER_REASON_UNHANDLED_HANDLER_ERROR`` under the status the handler picks, given positionally: UNAVAILABLE,
    DEADLINE_EXCEEDED or RESOURCE_EXHAUSTED; any other is a
    :class:`ValueError`. Its key and args are the cause, as a :class:`Bug`'s."""

    def __init__(self, key: str, status: int, /, **args: object) -> None:
        status = _check(status, _RETRYABLE, "a retryable bug")
        super().__init__(
            UNHANDLED_HANDLER_ERROR,
            status,
            flatten(reason_args.CAUSE, key, _strings(args)),
        )


class UnclassifiedError(Exception):
    """An error business code does not classify (Rust's unclassified
    handler error): ``ROUTER_REASON_UNHANDLED_HANDLER_ERROR`` with no cause.
    Its text stays in the handler's log. Any OTHER exception escaping business code is a
    panic, cause ``ROUTER_REASON_HANDLER_PANICKED`` (decision 14.42, C-0669)."""


def type_name(type_url: str) -> str:
    """The fully-qualified name a type URL names, whatever its prefix."""
    return type_url.rpartition("/")[2]


def pack(msg) -> any_pb2.Any:
    """``msg`` in an Any under the framework's bare-``/`` type-URL prefix."""
    return any_pb2.Any(
        type_url="/" + msg.DESCRIPTOR.full_name, value=msg.SerializeToString()
    )


def any_decode_error(type_url: str, cause: BaseException) -> CodedError:
    """INTERNAL ``ROUTER_REASON_ANY_DECODE_FAILED``: bytes that do not decode
    as the message ``type_url`` names (``message_type``), the protobuf runtime's
    own text carried verbatim as ``ext_err_txt``."""
    return CodedError(
        ANY_DECODE_FAILED,
        None,
        {
            reason_args.MESSAGE_TYPE: type_name(type_url),
            reason_args.EXT_ERR_TXT: str(cause),
        },
    )


def unknown_type(code: str, type_url: str) -> CodedError:
    """A type no handler claims, refused naming the type the router was
    given: the coordinator adds the type as sent (decision 14.39)."""
    return CodedError(code, None, {reason_args.TYPE_URL: type_url})


def escaped(exc: Exception) -> CodedError:
    """What an exception escaping business code is answered as: a coded
    error as itself, an :class:`UnclassifiedError` as
    ``ROUTER_REASON_UNHANDLED_HANDLER_ERROR`` with no cause, anything else as
    a panic. Logs what escaped."""
    if isinstance(exc, CodedError):
        return exc
    _log.error("handler_escaped", exc_info=exc)
    if isinstance(exc, UnclassifiedError):
        return CodedError(UNHANDLED_HANDLER_ERROR)
    return CodedError(
        UNHANDLED_HANDLER_ERROR, extras={reason_args.CAUSE: HANDLER_PANICKED}
    )


def as_cause(exc: Exception) -> dict[str, str]:
    """A failing applier's or fold's failure as the ``cause`` args of the
    ``ROUTER_REASON_FOLD_FAILED`` it causes: a handler bug's own cause (none
    for an unclassified error), any other coded error's key and args."""
    coded = escaped(exc)
    if coded.code == UNHANDLED_HANDLER_ERROR:
        return coded.extras
    return flatten(reason_args.CAUSE, coded.code, coded.extras)
