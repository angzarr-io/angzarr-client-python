# Tier 5 Phase 1 — Python unified Router

Reference implementation for the cross-language unified Router redesign. Sequential, test-driven, validated at each round before the next begins.

## Design (agreed)

- **One builder**: `Router[S](name)` with `.with_handler(cls, factory)` as the sole registration method. `factory` is a zero-arg `Callable[[], Any]` (lambda, bound method, partial, pool checkout, singleton closure) — invoked per dispatch call so handler state is isolated per request and the router is safe to share across threads.
- **Class decorators** declare the kind: `@command_handler`, `@saga`, `@process_manager`, `@projector`. No base classes.
- **Method decorators** declare behavior: `@handles`, `@applies`, `@rejected`, `@state_factory`.
- **Typed runtime routers** are the result of `Router.build()`: `CommandHandlerRouter[S]`, `SagaRouter`, `ProcessManagerRouter[S]`, `ProjectorRouter`. No public constructors.
- **Multi-handler dispatch**: multiple instances may claim the same `(domain, type_url)`; all get called in registration order, outputs merged, sequence numbers increment across the merged stream.
- **Mode enforcement**: all instances in one router must be the same kind; mixing raises `BuildError`.
- **CloudEvents**: deleted from Python (Rust-only going forward).
- **Scope of this phase**: `client-python/main/angzarr_client/` and its tests only. `examples-python` migration is a separate phase.

## Target surface

```python
# Builder (single entry point)
Router[S](name: str)
    .with_handler(cls: type, factory: Callable[[], Any]) -> Router
    .build() -> CommandHandlerRouter[S] | SagaRouter | ProcessManagerRouter[S] | ProjectorRouter

# Usage
Router("agg-service")
    .with_handler(Player, lambda: Player(db_pool))
    .with_handler(Hand, lambda: Hand(rng))
    .build()

# Class decorators (no base class)
@command_handler(domain: str, state: type[S])
@saga(name: str, source: str, target: str)
@process_manager(name: str, pm_domain: str, sources: list[str], targets: list[str], state: type[S])
@projector(name: str, domains: list[str])

# Method decorators
@handles(message_type: type)
@applies(event_type: type)
@rejected(domain: str, command: str)
@state_factory

# Runtime routers (no public constructors)
CommandHandlerRouter[S]   .dispatch(ContextualCommand) -> BusinessResponse
SagaRouter                .dispatch(SagaHandleRequest) -> SagaResponse
ProcessManagerRouter[S]   .dispatch(PMHandleRequest)   -> PMHandleResponse
ProjectorRouter           .dispatch(EventBook)         -> Projection
```

## File layout (end state)

```
angzarr_client/
  router/                                       NEW package
    __init__.py                                 re-exports
    builder.py                                  Router class
    decorators.py                               class + method decorators
    runtime.py                                  typed runtime routers
    dispatch.py                                 per-kind dispatch
    validation.py                               build-time invariants
  aggregate.py                                  DELETE
  saga.py                                       DELETE
  process_manager.py                            DELETE
  projector.py                                  DELETE
  cloudevents.py                                DELETE
  aggregate_handler.py                          REWRITE (new runtime)
  saga_handler.py                               REWRITE
  process_manager_handler.py                    REWRITE
  projector_handler.py                          REWRITE
  __init__.py                                   UPDATE exports

tests/
  router/                                       NEW
    test_decorators.py
    test_builder.py
    test_dispatch_command_handler.py
    test_multi_handler.py
    test_rejection.py
    test_dispatch_saga.py
    test_dispatch_pm.py
    test_dispatch_projector.py
  test_aggregate.py                             DELETE
  test_saga.py                                  DELETE
  test_process_manager.py                       DELETE
  test_projector.py                             DELETE
  test_unified_router.py                        DELETE (supplanted)
  test_aggregate_handler.py                     ADAPT
  test_saga_handler.py                          ADAPT
  test_process_manager_handler.py               ADAPT
  test_projector_handler.py                     ADAPT
  features/                                     unchanged — must stay green
```

## TDD rounds

Each round: (1) write tests, (2) run, confirm failing, (3) implement, (4) run, confirm green, (5) confirm earlier rounds still green, (6) advance.

### R1 — class decorators stash metadata
Tests: applying each of the four class decorators sets `__angzarr_kind__`, `__angzarr_domain__` (or kind-specific fields), `__angzarr_state__` where applicable. Stacking two class decorators on one class raises `TypeError`.
Implement: `router/decorators.py` (class decorators only).

### R2 — method decorators stash metadata
Tests: `@handles`, `@applies`, `@rejected`, `@state_factory` each set a distinct attribute on the decorated method. Stacking conflicting method decorators raises `TypeError`.
Implement: method decorators in `router/decorators.py`.

### R3 — builder collects factories
Tests: `Router(name).with_handler(cls, factory)` stores a `(cls, factory)` tuple. `.build()` on empty builder → `BuildError`. `.with_handler(cls, factory)` where `cls` has no `@command_handler`/`@saga`/etc. → `BuildError` naming the class. Factories are not invoked at registration or build time — only during dispatch.
Implement: `router/builder.py` — `Router`, `_factories: list[tuple[type, Callable[[], Any]]]`, build-time dispatch.

### R4 — mode inference
Tests: homogeneous factory lists (all classes share one kind) build to the correct runtime type (`CommandHandlerRouter`, `SagaRouter`, `ProcessManagerRouter`, `ProjectorRouter`). Mixed kinds → `BuildError("cannot mix ... in one router")`.
Implement: mode inference logic, stub runtime classes in `router/runtime.py`.

### R5 — build-time validation per kind
Tests (parametrized): missing required fields on class decorators raise `BuildError` at decoration or at build time, whichever is earlier.
- `@command_handler` missing `state`: error
- `@saga` missing `target`: error
- `@process_manager` missing `pm_domain` / `sources` / `targets` / `state`: error
- `@projector` missing `domains`: error
- Multiple factories with the same `(domain, type_url)`: **allowed** (call-both design)
Implement: `router/validation.py`.

### R6 — single-handler command dispatch
Tests: one `@command_handler` factory with a class carrying `@handles(Cmd)`. `router.dispatch(ContextualCommand)` returns `BusinessResponse` with the emitted event wrapped. The factory is invoked once per dispatch (only when a match is found). Unknown type_url → gRPC `INVALID_ARGUMENT`.
Implement: `CommandHandlerRouter.dispatch` (no state yet), `router/dispatch.py`.

### R7 — state rebuild via @applies
Tests: class with `@applies(Evt)` mutating state. Prior EventBook with `Evt` present → handler (produced by its factory) receives state reflecting the mutation. Custom `@state_factory` override works; defaults to `state()` when not specified.
Implement: pre-dispatch state rebuild loop in `CommandHandlerRouter`.

### R8 — multi-handler merge
Tests: two command-handler factories in the same domain produce classes both carrying `@handles(Cmd)`. Both invoked in registration order (one factory call per matched handler per dispatch). Events concatenated in registration order. Each instance's state rebuilt from its own `@applies` (isolated).
Implement: multi-handler fan-out in dispatch.

### R9 — sequence increments across handlers
Tests: handler A emits 2 events at `seq=5`; handler B is called with `seq=7`. Merged output has monotonically increasing sequences. Framework-driven, handlers don't coordinate.
Implement: `seq` tracking in dispatch loop.

### R10 — rejection handler routing
Tests: `@rejected("payment", "ProcessPayment")` method receives Notification. Multiple `@rejected` handlers for same `(domain, command)`: all invoked, compensation events merged.
Implement: notification branch in `CommandHandlerRouter` / `SagaRouter` / `ProcessManagerRouter`.

### R11 — saga dispatch
Tests: `@saga(source="order", target="inventory")` class with `@handles(OrderPlaced)` returning commands for "inventory". `SagaRouter.dispatch` wraps into `SagaResponse`. One factory call per matched handler per dispatch; multi-handler merge applies.
Implement: `SagaRouter.dispatch`.

### R12 — process-manager dispatch
Tests: `@process_manager` with multiple sources, pm_domain, state. State rebuilt per-instance via `@applies`. `@handles` emits commands + PM events. Destination-sequence stamping for outbound commands. Rejection path works.
Implement: `ProcessManagerRouter.dispatch`.

### R13 — projector dispatch
Tests: `@projector(domains=[...])` with `@handles` side-effecting through `self`. Multi-handler fan-out, no merge (side-effect semantics). No state. One projector instance per matching factory per dispatch, reused across every event in the book so projectors can batch writes within a single projection run.
Implement: `ProjectorRouter.dispatch`.

### R14 — gRPC server wrappers adapt
Tests: existing `tests/features/` Gherkin scenarios pass against the new runtime routers via adapted `aggregate_handler.py` / `saga_handler.py` / `process_manager_handler.py` / `projector_handler.py`.
Implement: rewrite the four `*_handler.py` modules to accept the new typed runtime routers.

### R15 — exports cleanup and deletions
Tests: old exports (`CommandHandler`, `Saga`, `ProcessManager`, `Projector`, `CommandRouter`, `EventRouter`, `FluentRouter`, `OORouter`, `SingleFluentRouter`, `CloudEvent*`) raise `ImportError`. New exports resolve.
Implement:
- Delete `aggregate.py`, `saga.py`, `process_manager.py`, `projector.py`, `cloudevents.py`, `router.py` (old flat module supplanted by `router/` package), `test_unified_router.py`, and the per-component old OO tests.
- Update `__init__.py` to new surface.
- Run the full `pytest` once end-to-end.

## Doneness gate per round

1. New round's tests green.
2. All prior rounds' tests still green.
3. No new `pyright` / `mypy` warnings introduced by the round's edits.
4. Task status updated.

## Risks + mitigations

- **Gherkin tests are the integration canary.** If R14 surfaces a regression, fix before advancing. Don't leave R14 half-done.
- **Sequence-number logic is easy to get wrong.** R9 has explicit tests; don't merge R9 with R8.
- **State rebuild with multi-handler dispatch.** Each handler instance (produced by its factory) must rebuild state independently; don't share state objects across factories.
- **Factory latency on the request path.** Factories run inside `dispatch()`; document that handlers with expensive construction (I/O, connection setup, config reads) should pool or close over a pre-built instance.
- **Type-URL suffix vs exact match semantics.** Preserve current suffix behavior for back-compat; document it.

## Out of scope for Phase 1

- `examples-python` migration
- Other language clients (Phase 2–6)
- Performance benchmarking
- gRPC wire-level integration tests (covered by Gherkin)
