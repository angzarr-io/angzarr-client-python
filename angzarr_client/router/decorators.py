"""Class-level decorators that stash metadata on handler classes.

Five kinds of components:

    @command_handler(domain=..., state=...)
    @saga(name=..., source=..., target=...)
    @process_manager(name=..., pm_domain=..., sources=[...], targets=[...], state=...)
    @projector(name=..., domains=[...])
    @upcaster(name=..., domain=...)

Each decorator:
  - sets ``cls.__angzarr_kind__`` to the kind string
  - sets ``cls.__angzarr_meta__`` to a dict of the decorator kwargs
  - raises ``TypeError`` if the class was already decorated with a different kind

Required kwargs are keyword-only with no defaults so Python raises ``TypeError``
on missing arguments — the same shape the tests rely on.
"""

from __future__ import annotations

from typing import Any, Callable, TypeVar

T = TypeVar("T", bound=type)


def _stamp(cls: type, kind: str, meta: dict[str, Any]) -> None:
    """Attach kind + meta attributes to the class; guard against double-decoration."""
    existing = getattr(cls, "__angzarr_kind__", None)
    if existing is not None:
        if existing == kind:
            raise TypeError(
                f"{cls.__name__} is already decorated with @{kind}; "
                f"angzarr components may only be decorated once"
            )
        raise TypeError(
            f"{cls.__name__} is already decorated as @{existing}; "
            f"cannot also decorate as @{kind}"
        )
    cls.__angzarr_kind__ = kind  # type: ignore[attr-defined]
    cls.__angzarr_meta__ = meta  # type: ignore[attr-defined]


def command_handler(
    *, domain: str, state: type, supports_replay: bool = False
) -> Callable[[T], T]:
    """Mark a class as a command handler (aggregate) for ``domain``.

    The ``state`` type is the aggregate's state type; the class must either
    provide a ``@state_factory`` method or rely on ``state()`` as the default
    factory (enforced in a later round).

    ``supports_replay`` (default ``False``) opts the aggregate into the
    coordinator's ``Replay`` RPC, used for ``MERGE_COMMUTATIVE`` conflict
    detection. When ``True``, the framework auto-implements replay using
    the ``@applies`` methods to rebuild state from a snapshot + events;
    the state type must be a proto Message (carry a ``DESCRIPTOR``).
    When ``False`` the gRPC adapter returns ``UNIMPLEMENTED`` for
    ``Replay`` requests — the coordinator degrades to ``MERGE_STRICT``
    semantics. Audit #45.

    Fact handling is opt-in via the ``@handles_fact(EventType)`` method
    decorator. Aggregates with no ``@handles_fact`` methods get
    ``UNIMPLEMENTED`` from the gRPC adapter for ``HandleFact``; the
    coordinator falls back to pass-through-persist (per the proto's
    Optional contract).
    """

    def decorate(cls: T) -> T:
        _stamp(
            cls,
            "command_handler",
            {"domain": domain, "state": state, "supports_replay": supports_replay},
        )
        return cls

    return decorate


def saga(
    *, name: str, source: str, target: str, sync: bool = False
) -> Callable[[T], T]:
    """Mark a class as a saga translating events from ``source`` to commands
    for ``target``.

    Audit #74: ``sync`` declares whether commands emitted to ``target``
    ever use sync mode (SIMPLE / CASCADE / DECISION / ISOLATED). Default
    ``False`` — target commands flow through the async bus and the
    readiness supervisor will not probe ``target``'s coordinator. Flip to
    ``True`` when the saga blocks on the downstream response.
    """

    def decorate(cls: T) -> T:
        _stamp(
            cls,
            "saga",
            {"name": name, "source": source, "target": target, "sync": sync},
        )
        return cls

    return decorate


def process_manager(
    *,
    name: str,
    pm_domain: str,
    sources: list[str],
    targets: list[str],
    state: type,
    sync_targets: list[str] | None = None,
) -> Callable[[T], T]:
    """Mark a class as a process manager.

    ``pm_domain`` is the PM's own state-storage domain; ``sources`` lists
    incoming event domains; ``targets`` lists downstream command domains.

    Audit #74: ``sync_targets`` declares the subset of ``targets`` the PM
    ever addresses with sync mode (SIMPLE / CASCADE / DECISION /
    ISOLATED) — those are the targets whose coordinator must be reachable
    for readiness. Default ``None`` (no sync targets) — every command
    goes through the async bus and the readiness supervisor will not
    probe any target's coordinator.

    Raises ``ValueError`` if ``sync_targets`` contains a domain that is
    not in ``targets``.
    """
    sync_set = list(sync_targets) if sync_targets else []
    extra = [t for t in sync_set if t not in targets]
    if extra:
        raise ValueError(f"sync_targets {extra!r} are not in targets {targets!r}")

    def decorate(cls: T) -> T:
        _stamp(
            cls,
            "process_manager",
            {
                "name": name,
                "pm_domain": pm_domain,
                "sources": sources,
                "targets": targets,
                "state": state,
                "sync_targets": sync_set,
            },
        )
        return cls

    return decorate


def projector(*, name: str, domains: list[str]) -> Callable[[T], T]:
    """Mark a class as a projector consuming events from ``domains``."""

    def decorate(cls: T) -> T:
        _stamp(cls, "projector", {"name": name, "domains": domains})
        return cls

    return decorate


def upcaster(*, name: str, domain: str) -> Callable[[T], T]:
    """Mark a class as an upcaster transforming events in ``domain``.

    Methods decorated with ``@upcasts(FromType, ToType)`` declare individual
    version-to-version transformations. An upcaster with zero ``@upcasts``
    methods is allowed (passthrough).
    """

    def decorate(cls: T) -> T:
        _stamp(cls, "upcaster", {"name": name, "domain": domain})
        return cls

    return decorate


# --------------------------------------------------------------------------
# Method-level decorators
# --------------------------------------------------------------------------

_METHOD_SENTINELS = (
    "__angzarr_handles__",
    "__angzarr_handles_fact__",
    "__angzarr_applies__",
    "__angzarr_rejected__",
    "__angzarr_state_factory__",
    "__angzarr_upcasts__",
)


def _guard_method(fn: Callable[..., Any], new_role: str) -> None:
    """Raise TypeError if ``fn`` already carries any other method-decorator sentinel."""
    for sentinel in _METHOD_SENTINELS:
        if getattr(fn, sentinel, None) is not None:
            raise TypeError(
                f"{fn.__name__} is already decorated with {sentinel!r}; "
                f"cannot also decorate with {new_role!r}"
            )


F = TypeVar("F", bound=Callable[..., Any])


def handles(message_type: type) -> Callable[[F], F]:
    """Register a method as a dispatch target for ``message_type``.

    For command handlers this is the command type; for sagas / process managers /
    projectors it is the event type. Dispatch routes by proto full-name match.
    """

    def decorate(fn: F) -> F:
        _guard_method(fn, "@handles")
        fn.__angzarr_handles__ = message_type  # type: ignore[attr-defined]
        return fn

    return decorate


def handles_fact(event_type: type) -> Callable[[F], F]:
    """Register a method as a fact-event handler for ``event_type``.

    Audit #45. Triggered when the coordinator dispatches a fact (an
    external reality, e.g. a payment confirmation from a third party)
    via the ``HandleFact`` RPC. The method receives ``(event, state)``
    after state has been rebuilt from prior events; it returns the
    events to persist (or ``None`` for pure side-effects).

    Aggregates with at least one ``@handles_fact`` method opt into the
    ``HandleFact`` RPC. Aggregates with none get ``UNIMPLEMENTED`` from
    the framework's gRPC adapter — the coordinator then falls back to
    pass-through-persist per the proto's Optional contract.

    Distinct from ``@handles`` (which dispatches commands and returns
    events), ``@handles_fact`` is fact-specific: facts cannot be
    rejected, and the method's return shape is just events-to-persist.
    """

    def decorate(fn: F) -> F:
        _guard_method(fn, "@handles_fact")
        fn.__angzarr_handles_fact__ = event_type  # type: ignore[attr-defined]
        return fn

    return decorate


def applies(event_type: type) -> Callable[[F], F]:
    """Register a method as a state applier for ``event_type``.

    Appliers are invoked during state rebuild, walking the prior event book
    and mutating the instance's state in place.
    """

    def decorate(fn: F) -> F:
        _guard_method(fn, "@applies")
        fn.__angzarr_applies__ = event_type  # type: ignore[attr-defined]
        return fn

    return decorate


def rejected(source_domain: str, command: str) -> Callable[[F], F]:
    """Register a method as a compensation handler for command rejections.

    Triggered when a command originating from this component is rejected by
    the target aggregate. ``source_domain`` and ``command`` identify the
    rejected command's proto full-name suffix split into domain/command parts.
    """

    def decorate(fn: F) -> F:
        _guard_method(fn, "@rejected")
        fn.__angzarr_rejected__ = (source_domain, command)  # type: ignore[attr-defined]
        return fn

    return decorate


def state_factory(fn: F) -> F:
    """Mark a method as the state factory for this instance.

    Overrides the default factory (calling ``StateType()``). Useful when state
    construction requires parameters or custom defaults.
    """
    _guard_method(fn, "@state_factory")
    fn.__angzarr_state_factory__ = True  # type: ignore[attr-defined]
    return fn


def upcasts(from_type: type, to_type: type) -> Callable[[F], F]:
    """Register a method as a transformation from ``from_type`` to ``to_type``.

    The method must accept the old event and return the new one. Dispatch
    matches by exact proto type-URL on the incoming event; events without a
    registered transform pass through unchanged.
    """

    def decorate(fn: F) -> F:
        _guard_method(fn, "@upcasts")
        fn.__angzarr_upcasts__ = (from_type, to_type)  # type: ignore[attr-defined]
        return fn

    return decorate
