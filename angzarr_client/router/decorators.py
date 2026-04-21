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


def command_handler(*, domain: str, state: type) -> Callable[[T], T]:
    """Mark a class as a command handler (aggregate) for ``domain``.

    The ``state`` type is the aggregate's state type; the class must either
    provide a ``@state_factory`` method or rely on ``state()`` as the default
    factory (enforced in a later round).
    """

    def decorate(cls: T) -> T:
        _stamp(cls, "command_handler", {"domain": domain, "state": state})
        return cls

    return decorate


def saga(*, name: str, source: str, target: str) -> Callable[[T], T]:
    """Mark a class as a saga translating events from ``source`` to commands
    for ``target``.
    """

    def decorate(cls: T) -> T:
        _stamp(cls, "saga", {"name": name, "source": source, "target": target})
        return cls

    return decorate


def process_manager(
    *,
    name: str,
    pm_domain: str,
    sources: list[str],
    targets: list[str],
    state: type,
) -> Callable[[T], T]:
    """Mark a class as a process manager.

    ``pm_domain`` is the PM's own state-storage domain; ``sources`` lists
    incoming event domains; ``targets`` lists downstream command domains.
    """

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
