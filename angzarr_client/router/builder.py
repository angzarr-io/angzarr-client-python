"""Unified Router builder.

Registration-only surface in R3. Mode inference + kind validation arrive in
R4 / R5; dispatch logic arrives in R6+. See TIER5_PLAN.md for the full round
sequence.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable


class BuildError(Exception):
    """Raised when the builder cannot produce a valid runtime router.

    Audit #72: carries the structural error shape from audit #59 —
    SCREAMING_SNAKE ``code`` (programmatic dispatch / cucumber
    assertions), static ``message`` (cross-language equality), and
    ``details`` for runtime context (router name, conflicting kinds,
    duplicate (domain, type_url), handler class, field name, etc.).
    Mirrors Rust ``router::handler::BuildError`` which carries an
    ``ErrorDetail`` per variant.

    Construction sites pass constants from ``angzarr_client.error_codes``:
    ``codes.DUPLICATE_COMMAND_HANDLER`` etc. for the code,
    ``messages.DUPLICATE_COMMAND_HANDLER`` etc. for the static message,
    detail keys from ``keys`` for the details map.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "",
        details: Mapping[str, Any] | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details: dict[str, str] = {k: str(v) for k, v in (details or {}).items()}

    def __str__(self) -> str:
        # Static message only — no concatenation. Audit #59 / #72.
        return self.message


class Router:
    """Fluent builder that collects handler factories for the unified router.

    Usage::

        Router("agg-service")
            .with_handler(Player, lambda: Player(db_pool))
            .with_handler(Hand, lambda: Hand(rng))
            .build()

    ``.build()`` returns a typed runtime router (``CommandHandlerRouter``,
    ``SagaRouter``, ``ProcessManagerRouter``, or ``ProjectorRouter``). Mixed
    handler kinds are rejected at build time (R4).

    Handlers are registered as ``(cls, factory)`` pairs so the router can
    invoke ``factory()`` per dispatch call to obtain a fresh (or pooled)
    instance. This keeps handler state isolated per request and makes the
    router safe to share across threads.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self._factories: list[tuple[type, Callable[[], Any]]] = []

    def with_handler(self, cls: type, factory: Callable[[], Any]) -> "Router":
        """Register a handler class together with a zero-arg factory.

        ``cls`` must carry one of the five kind decorators
        (``@command_handler``, ``@saga``, ``@process_manager``,
        ``@projector``, ``@upcaster``). Registering an undecorated class
        raises :class:`BuildError`.

        ``factory`` is any ``Callable[[], Any]``: a lambda, a bound method on
        a DI container, a ``functools.partial``, a pool-checkout closure, or
        a callable returning a shared singleton. The router never inspects
        the factory body; it only calls it and uses whatever it returns.

        Factories are invoked inside ``dispatch()``, so their latency is on
        the request path. For handlers whose construction is expensive —
        opens a DB connection, reads a config file, performs I/O, does
        non-trivial computation, holds resources that must be released —
        prefer a pool checkout or close over a pre-built instance rather
        than constructing fresh on every call.
        """
        # Audit #72: structural error model — code + static message + details.
        from angzarr_client.error_codes import codes, keys, messages

        kind = getattr(cls, "__angzarr_kind__", None)
        if kind is None:
            raise BuildError(
                messages.HANDLER_UNKNOWN_KIND,
                code=codes.HANDLER_UNKNOWN_KIND,
                details={
                    keys.HANDLER_CLASS: cls.__name__,
                    keys.HANDLER_KIND: "<undecorated>",
                },
            )
        self._factories.append((cls, factory))
        return self

    def build(self) -> Any:
        """Produce a typed runtime router.

        Empty → :class:`BuildError`. Mixed kinds → :class:`BuildError`.
        Homogeneous → ``CommandHandlerRouter`` / ``SagaRouter`` /
        ``ProcessManagerRouter`` / ``ProjectorRouter`` per the shared kind.
        """
        from angzarr_client.error_codes import codes, keys, messages

        if not self._factories:
            raise BuildError(
                messages.ROUTER_NO_HANDLERS,
                code=codes.ROUTER_NO_HANDLERS,
                details={keys.ROUTER_NAME: self.name},
            )

        kinds = {cls.__angzarr_kind__ for cls, _ in self._factories}
        if len(kinds) > 1:
            sorted_kinds = sorted(kinds)
            raise BuildError(
                messages.MIXED_HANDLER_KINDS,
                code=codes.MIXED_HANDLER_KINDS,
                details={
                    keys.HANDLER_KIND: sorted_kinds[0],
                    keys.OTHER_KIND: sorted_kinds[1],
                    keys.ROUTER_NAME: self.name,
                },
            )

        # Deferred imports avoid circular references through __init__.
        from .runtime import (
            CommandHandlerRouter,
            ProcessManagerRouter,
            ProjectorRouter,
            SagaRouter,
            UpcasterRouter,
        )
        from .validation import validate_handler

        for cls, _ in self._factories:
            validate_handler(cls)

        (kind,) = kinds

        # Audit finding #18 (formerly #51): at most one CommandHandler
        # per (domain, command_type_url) within a Router. Saga / PM /
        # projector / upcaster fan-out is unaffected.
        #
        # Cross-language contract (cucumber C-0065): each CH factory is
        # invoked at most once at build time. Python could skip the
        # factory call here (class attributes carry the metadata
        # directly), but invoking it preserves the Rust contract — Rust
        # MUST call the factory to introspect its `Handler::config()`,
        # since the metadata isn't statically reachable on the
        # type. Symmetric behavior keeps the cucumber assertion
        # ("factory invocation count is 1") true on both sides.
        if kind == "command_handler":
            from ..helpers import full_type_url_for

            seen: set[tuple[str, str]] = set()
            for cls, factory in self._factories:
                # Invoke the factory once for config introspection (parity
                # with Rust). Discard the instance.
                _ = factory()
                meta = cls.__angzarr_meta__
                domain = meta.get("domain", "")
                for attr_name in dir(cls):
                    method = getattr(cls, attr_name, None)
                    handled_type = getattr(method, "__angzarr_handles__", None)
                    if handled_type is None:
                        continue
                    type_url = full_type_url_for(handled_type)
                    key = (domain, type_url)
                    if key in seen:
                        raise BuildError(
                            messages.DUPLICATE_COMMAND_HANDLER,
                            code=codes.DUPLICATE_COMMAND_HANDLER,
                            details={
                                keys.DOMAIN: domain,
                                keys.TYPE_URL: type_url,
                                keys.ROUTER_NAME: self.name,
                            },
                        )
                    seen.add(key)

        if kind == "command_handler":
            return CommandHandlerRouter(self._factories)
        if kind == "saga":
            return SagaRouter(self._factories)
        if kind == "process_manager":
            return ProcessManagerRouter(self._factories)
        if kind == "projector":
            return ProjectorRouter(self._factories)
        if kind == "upcaster":
            return UpcasterRouter(self._factories)
        raise BuildError(
            messages.HANDLER_UNKNOWN_KIND,
            code=codes.HANDLER_UNKNOWN_KIND,
            details={keys.HANDLER_KIND: str(kind)},
        )
