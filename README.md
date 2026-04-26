> **⚠️ Notice:** This repository was recently extracted from the [angzarr monorepo](https://github.com/angzarr-io/angzarr) and has not yet been validated as a standalone project. Expect rough edges. See the [Angzarr documentation](https://angzarr.io/) for more information.

---
title: Python SDK
sidebar_label: Python
---

# angzarr-client

Python client library for the Angzarr CQRS/ES framework.

:::tip Unified Documentation
For cross-language API reference with side-by-side comparisons, see the [SDK Documentation](/sdks).
:::

## Installation

```bash
pip install angzarr-client
```

## Quick Start

```python
from angzarr_client import DomainClient
from uuid import uuid4

client = DomainClient.connect("localhost:1310")

# Build and execute a command
order_id = uuid4()
response = (
    client.command_handler
    .command("order", order_id)
    .with_command("type.googleapis.com/examples.CreateOrder", create_order_msg)
    .execute()
)

# Query events
events = client.query.query("order", order_id).get_event_book()
```

## Handler Kinds

Handler classes are declared with class decorators. No base class is required — the decorator stamps metadata the router reads at build time.

| Kind | Decorator | Purpose |
|------|-----------|---------|
| Command handler | `@command_handler(domain, state)` | Validate commands, emit events |
| Saga | `@saga(name, source, target)` | Translate source-domain events into target-domain commands |
| Process manager | `@process_manager(name, pm_domain, sources, targets, state)` | Stateful multi-domain orchestrator |
| Projector | `@projector(name, domains)` | Side-effect fan-out over event books |
| Upcaster | `@upcaster(name, domain)` | Transform legacy event versions in place |

Method markers inside the class:

| Marker | Applies to | Role |
|--------|-----------|------|
| `@handles(MessageType)` | Any kind | Register a handler for an incoming message |
| `@applies(EventType)` | command_handler, process_manager | Mutate state during replay |
| `@rejected(domain, command)` | command_handler, saga, process_manager | Receive a rejection notification and emit compensation |
| `@state_factory` | command_handler, process_manager | Override `state()` default for initial state |
| `@upcasts(from, to)` | upcaster | Transform one event type into another |

### Command-handler example

```python
from dataclasses import dataclass
from angzarr_client import command_handler, handles, applies
from angzarr_client.errors import CommandRejectedError

@dataclass
class PlayerState:
    player_id: str = ""
    bankroll: int = 0

@command_handler(domain="player", state=PlayerState)
class Player:
    def __init__(self, db_pool):
        self.db_pool = db_pool

    @applies(PlayerRegistered)
    def apply_registered(self, state: PlayerState, evt: PlayerRegistered) -> None:
        state.player_id = evt.player_id

    @handles(RegisterPlayer)
    def register(self, cmd: RegisterPlayer, state: PlayerState, seq: int) -> EventBook:
        if state.player_id:
            raise CommandRejectedError.precondition_failed("player already exists")
        # build and return the event book
        ...
```

## Router

One builder, one entry point:

```python
from angzarr_client import Router
from angzarr_client.router import CommandHandlerGrpc
from angzarr_client import run_server
from angzarr_client.proto.angzarr import command_handler_pb2_grpc

built = (
    Router("agg-player")
    .with_handler(Player, lambda: Player(db_pool))
    .with_handler(Hand, lambda: Hand(rng))
    .build()
)

# Wrap the runtime router in the matching gRPC adapter, then serve it.
servicer = CommandHandlerGrpc(built)
run_server(
    command_handler_pb2_grpc.add_CommandHandlerServiceServicer_to_server,
    servicer,
    service_name="CommandHandler",
    domain="player",
)
```

The factory callable runs once per dispatch, so each request gets a fresh handler instance. Close over shared deps (`lambda: Player(db_pool)`) or hand in a pool-checkout callable.

`Router.build()` returns a `CommandHandlerRouter / SagaRouter / ProcessManagerRouter / ProjectorRouter / UpcasterRouter` based on the kinds present. Mixing kinds in one router raises `BuildError("cannot mix ...")`.

## Clients

| Client | Purpose |
|--------|---------|
| `CommandHandlerClient` | Send commands to a coordinator |
| `QueryClient` | Fetch event books |
| `SpeculativeClient` | Dry-run commands without persisting |
| `DomainClient` | Bundle of all three, scoped to a domain |

All four carry a `connect(endpoint, retry=None)` classmethod, a `from_channel(channel)` for caller-managed channels, and a `from_env(env_var, default)` helper.

## Error handling

```python
from angzarr_client.errors import ClientError, GRPCError, ConnectionError

try:
    response = client.command_handler.handle(cmd)
except GRPCError as e:
    if e.is_precondition_failed():
        # Sequence mismatch (optimistic locking)
        ...
    elif e.is_not_found():
        # Aggregate missing
        ...
    elif e.is_invalid_argument():
        # Bad input
        ...
except ConnectionError:
    # Transport failure
    ...
```

`ClientError` is the base exception. `GRPCError / ConnectionError / TransportError / InvalidArgumentError / InvalidTimestampError / CommandRejectedError` inherit from it. All instances expose `is_not_found() / is_precondition_failed() / is_invalid_argument() / is_connection_error()` predicates. `CommandRejectedError` adds named factory methods — `precondition_failed(msg)`, `invalid_argument(msg)`, `not_found(msg)`.

## Retry

```python
from angzarr_client import ExponentialBackoffRetry

policy = ExponentialBackoffRetry(
    max_attempts=5,
    max_delay=2.0,
    on_retry=lambda i, e: print(f"retry {i}: {e}"),
)

result = policy.execute(lambda: try_something())
```

Defaults match the cross-language spec: 10 attempts, 100 ms → 5 s with jitter. `RetryPolicy` is an alias for `ExponentialBackoffRetry`.

## Coming from Rust?

Everything maps. The shape differs; the names and semantics don't.

| Concept | Rust | Python |
|---------|------|--------|
| Kind declaration | `#[command_handler(domain = "p", state = PlayerState)]` attribute macro on `impl` | `@command_handler(domain="p", state=PlayerState)` class decorator |
| Method marker | `#[handles(Cmd)]` | `@handles(Cmd)` |
| Router | `Router::new("x").with_handler::<H, _>(factory)` (type inferred) | `Router("x").with_handler(cls, factory)` (cls passed explicitly) |
| Factory | `\|\| Player::new(db.clone())` | `lambda: Player(db)` |
| Handler state | Struct instance from factory closure | Class instance |
| Cover accessors | Extension trait methods: `cover.domain()`, `cover.correlation_id()`, … (via `CoverExt`) | Free functions: `domain(cover)`, `correlation_id(cover)`, … |
| Event book helpers | Extension trait methods: `book.next_sequence()` (via `EventBookExt`) | Free functions: `next_sequence(book)` |
| Wrapper objects | Extension trait method directly: `cover.domain()` | `CoverW(cover).domain()` wrapper |
| Error surface | `thiserror` enum `ClientError { Connection, Transport, Grpc, InvalidArgument, InvalidTimestamp }` with `is_*` predicate methods | Exception hierarchy: `ClientError → GRPCError / ConnectionError / …` |
| Rejection factories | `CommandRejectedError::precondition_failed(msg)` | `CommandRejectedError.precondition_failed(msg)` |
| Retry | `ExponentialBackoffRetry::default().with_max_attempts(5)` | `ExponentialBackoffRetry(max_attempts=5)` |
| Retry callback | `.with_on_retry(\|i, e\| ...)` | `on_retry=lambda i, e: ...` |
| Compensation options | `delegate_to_framework(reason)` **or** `delegate_to_framework_with_options(reason, emit, send_to_dead_letter, escalate, abort)` (two-function idiom) | `delegate_to_framework(reason, send_to_dead_letter=True)` (kwargs) |
| Type-URL match | `type_url_matches(url, name)` (primary) — `type_url_matches_exact` is Python-compat alias | `type_url_matches(url, name)` (primary) |
| Destinations query | `destinations.has_domain(d)` (canonical) — `has_sequence` is a `#[deprecated]` alias | `destinations.has_domain(d)` |
| Destinations iteration | `destinations.domains()` — insertion-preserving via `IndexMap` | `destinations.domains` — insertion-preserving via `dict` |
| `CommandBuilder.execute` | `async fn execute() -> Result<CommandResponse>` — `with_sync_mode(SyncMode)` for non-default modes | `def execute(sync_mode=SyncMode.SYNC_MODE_ASYNC) -> CommandResponse` (sync) |
| `decode_event` | `decode_event(event, full_type_name)` — exact match (not suffix) | `decode_event(page, full_type_name, msg_class)` — exact match |
| Validation primitives | `require_positive<T: PartialOrd>(value, msg)` — generic | `require_positive(value: int|float|Decimal, msg)` |

## Things that intentionally differ (not bugs)

These are language-natural patterns that the audit flagged for
documentation rather than convergence (per audit P3.3 and P4):

- **Async vs sync `execute()`** — Rust is async (tonic-only); Python is
  sync (gRPC sync stub). Use Rust's `.await` or Python's blocking call.
- **Per-RPC timeout** — Python `handle_command(request, timeout=...)`
  passes timeout to the gRPC call; Rust uses tonic's request-level
  metadata mechanism. Set on the call site, not on the builder.
- **`unpack` / `try_unpack` style** — Rust returns `Result<T, …>` /
  `Option<T>`; Python raises `ValueError` / returns `None`. Same
  semantics, idiomatic per language.

## Saga / PM design philosophy

**Sagas and PMs are coordinators, not decision makers.**

| Output | When to use |
|--------|-------------|
| Commands (preferred) | Normal flow — the target aggregate validates and decides |
| Facts | Inject external data the target aggregate can't derive |

Key principles:

1. **Don't rebuild destination state** — use `Destinations` for sequences only.
2. **Let aggregates decide** — business logic in aggregates, not coordinators.
3. **Prefer commands with sync mode** — use `SyncMode.SIMPLE` for immediate feedback.
4. **Use facts sparingly** — only for external data injection.

## Speculative execution

Test commands without persisting to the event store.

```python
from angzarr_client import SpeculativeClient
from angzarr_client.proto.angzarr import SpeculateCommandHandlerRequest

client = SpeculativeClient.connect("localhost:1310")
request = SpeculateCommandHandlerRequest(
    command=command_book,
    events=prior_events,
)
response = client.command_handler(request)
```

## License

BSD-3-Clause

## Development

Install git hooks (requires [lefthook](https://github.com/evilmartians/lefthook)):

```bash
lefthook install
```

This configures a pre-commit hook that auto-formats code before each commit.

### Recipes

```bash
just -l              # list recipes
just build           # build the package
just test            # run pytest + BDD
just fmt             # ruff + black check
just fmt-fix         # auto-format
just mutation-test   # mutmut (80% kill-rate threshold)
```
