# Proposal: conflict handling, the build version, and command modes

**Status:** proposed, not implemented. Awaiting owner sign-off, to be
reviewed alongside the client-rust equivalent.

**Spec:** angzarr-project `spec/20261019-draft` (619933c).

- `types.proto`: `Handling`, `ConflictResolution`, `ContextualCommand.conflict_nonce`.
- `options.proto`: the `conflict_units` region (`changed`).
- `command_handler.proto`: the `command_modes` region (`Handle`, `HandleAsync`, `HandleSync`).
- Decisions 12.7 and 14.1–14.9.
- Scenarios: `handling.feature` C-0533..C-0550 and `aggregate_client.feature` C-0197 and C-0198.

**Red tests:** each one is a strict xfail citing its spec id. Implementing a
symbol makes its test pass, and the XPASS then fails the run until the
marker is removed.

- `tests/router/test_handling_proposal.py`
- `tests/router/test_rules_version_proposal.py`
- `tests/test_sender_api_proposal.py`
- The client feature scenarios this API unblocks, which are strict xfails in
  `tests/client/conftest.py`:
  - C-0197 and C-0198: the command modes;
  - C-0307, C-0308 and C-0311: an expected-sequence setter on the builder;
  - C-0315 and C-0316: merge strategy, whose successor is
    `with_conflict_resolution`. The feature text still says "merge
    strategy" and needs a spec update.

Every name comes from the spec or from the router core's Rust API
(angzarr-router `docs/design-handling.md`). None is invented here.

## 1. Handlers: the resolved Handling and conflicts

| Symbol | Spec / Rust source | Meaning |
|---|---|---|
| `CommandContext.expected_sequence: int` | `Handling.expected_sequence`; Rust `CommandContext.expected_sequence` | The resolved head the command was issued against. |
| `CommandContext.conflict_resolution: int` (`ConflictResolution`) | `Handling.conflict_resolution` | The resolved strategy. |
| `CommandContext.conflict: Conflict \| None` | Rust `CommandContext.conflict` | Set only under AGGREGATE_HANDLES. |
| `Conflict.window: list[EventPage]`, `Conflict.nonce: bytes` | `ContextualCommand.conflict_nonce`; Rust `Conflict::{window, nonce}` | The events the command did not see, and the nonce delivered with it. |
| `Conflict.resolve_conflicts(window, nonce)` | decision 12.7 `resolve_conflicts(window, nonce)` | Declares the window settled. The binding returns the nonce as `CommandOutput.resolved_nonce`. |
| `AggregateDispatch.with_commutative(changed)` | options.proto `changed`; Rust `with_commutative(changed)` | `changed(before, after) -> list[str]` over two decoded states. The binding registers it as `changed_callback_id`, and the router runs the overlap rule. |

```python
def add_item(cmd, state, cctx):
    if cctx.conflict is not None:          # AGGREGATE_HANDLES
        if any(touches(page, cmd.item) for page in cctx.conflict.window):
            raise reject("ITEM_CHANGED", "the item changed since you read it")
        cctx.conflict.resolve_conflicts(cctx.conflict.window, cctx.conflict.nonce)
    return [ItemAdded(item=cmd.item)]

dispatch = new_order_aggregate_dispatch(handler).with_commutative(order_changed)
```

Seam (not settled here): the generated wiring should register the generated
`changed()` (options.proto: "the generated `changed()`"). Python codegen
would need the CLI's template model to carry each state's conflict units.
Until it does, `changed` is hand-written, as the router's own conformance
fixture does. See "Open" below.

## 2. The rules version: baked in at build

The business binary's full version is baked in at build. versionator
generates a `_version.py` that the build writes and the package imports:

```sh
versionator output emit python --metadata > mypkg/_version.py
```

The host is given that version once, and stamps it on every component it
hosts. Co-hosted components share one binary, so they share one version
(decision 14.1).

| Symbol | Meaning |
|---|---|
| `ComponentHost(router=None, *, rules_version: str, ...)` | Applies `rules_version` to every `add_*` table that has none of its own (`with_rules_version`, already in the port). |
| Start-time check | `start()` fetches the router's hellos (`Router.component_descriptors()`). A missing version fails with `ConfigurationError` naming `MISSING_RULES_VERSION` (decision 14.6), before the server binds. |

```python
from mypkg._version import __version__

host = ComponentHost(rules_version=__version__)
host.add_aggregate(new_order_aggregate_dispatch(OrderAggregate()))
host.run()
```

`with_rules_version(version)` on each dispatch table (`AggregateDispatch`,
`SagaDispatch`, `ProcessManagerDispatch`, `ProjectorDispatch`) is already
part of the port. It mirrors Rust `with_rules_version` and is how
`ComponentHost` would apply the version.

## 3. Senders: Handling and the command mode

| Symbol | Spec source | Meaning |
|---|---|---|
| `CommandMode.ASYNC`, `CommandMode.DECISION`, `CommandMode.SIMPLE` | command_modes region | Selects `HandleAsync`, `Handle` or `HandleSync`. The mode is never sent on the wire. |
| `CommandHandlerClient.handle(book, mode=CommandMode.DECISION, timeout=None)` | "a single handle(book, mode) call as API sugar" | DECISION and SIMPLE return the `CommandResponse`; under SIMPLE, `outcome.projections` and `outcome.projector_failures` are filled. ASYNC returns `None`. |
| `CommandBuilder.execute(mode=CommandMode.DECISION)`, `DomainClient.execute(book, mode=...)` | | The same choice, made from the builder and the combined client. |
| `CommandBuilder.with_expected_sequence(seq)` | `Handling.expected_sequence` (optional) | Sets the field, so 0 is sent as set. Left unset, the coordinator stamps the head. |
| `CommandBuilder.with_conflict_resolution(resolution)` | `Handling.conflict_resolution` | Unset means the configured default. |
| `angzarr_client.ConflictResolution` | `types.proto` `ConflictResolution` | Re-exports the generated enum (`CONFLICT_RESOLUTION_STRICT`, …). |

```python
from angzarr_client import CommandMode, ConflictResolution, DomainClient

client = DomainClient.connect("localhost:1310")
response = (
    client.command_handler.command("orders", order_root)
    .with_command("/orders.v1.AddItem", AddItem(item="apple"))
    .with_expected_sequence(seen_next_sequence)
    .with_conflict_resolution(ConflictResolution.CONFLICT_RESOLUTION_COMMUTATIVE)
    .execute(mode=CommandMode.SIMPLE)
)
view = response.outcome.projections
```

The builder only builds requests. The coordinator refuses Handling on a fact
or on a reaction command, and the builder does not duplicate that check.

## Rejected alternatives

- **Three RPC-shaped methods (`handle`, `handle_async`, `handle_sync`) as
  the public API.** The spec names one `handle(book, mode)` call as the
  sugar, and the modes are named ASYNC, DECISION and SIMPLE, not after
  the RPCs. One method keeps the mode a single choice that the builder
  and `DomainClient` pass through. Its cost is a return type that depends
  on the mode (`None` for ASYNC).
- **A boolean or `wait_for=` keyword** instead of `CommandMode`. It
  invents vocabulary the spec does not have.
- **A `Handling` object passed to `execute()`.** Handling belongs to the
  `CommandBook`, which the builder builds, and it is never a call-level
  choice. Setting it from the builder keeps `build()` the whole request.
- **The rules version read from the installed distribution's metadata**
  (`importlib.metadata.version`). That is the library's version, not the
  business binary's. versionator's emitted module is the build value
  decision 14.1 asks for.
- **A `rules_version` per dispatch table only.** That is still available
  (`with_rules_version`), but it would let co-hosted components disagree,
  and the router reports one version for co-hosted process managers.
- **A Python-side COMMUTATIVE resolver.** Rejected in the router's design:
  the overlap rule exists once, in Rust. Python supplies only
  `changed()`.
- **Re-entrant resolve.** Not possible across the ABI. The acknowledgement
  travels as data (`CommandOutput.resolved_nonce`).

## Open

- **Codegen (CLI seam).** Two items depend on what the angzarr CLI gives
  the templates.
  - Generating `changed()` needs the conflict units in the template model.
  - Threading `rules_version` through `register_<component>(router,
    handler, *, rules_version)` needs only a template change in this
    repository. It is still deferred, because `ComponentHost(rules_version=)`
    covers hosting without it.
- **Mirroring Rust.** This proposal should match client-rust's sender API
  name for name: `CommandMode`, its variant names, `with_expected_sequence`
  and `with_conflict_resolution`. client-rust's in-progress port keeps
  `with_sequence`, mapped to `Handling.expected_sequence`. The spec's name
  is `expected_sequence`, so this proposal uses `with_expected_sequence`;
  one of the two has to change.
