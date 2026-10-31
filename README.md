# angzarr-client (Python)

The home of [Angzarr](https://angzarr.io) for Python:

- **`angzarr_client.router`** — the router, native Python (decision
  14.42). It hosts one component (an aggregate, a process manager, a policy
  or a projector) and does only dispatch: it routes by type URL, folds the
  starting state and the pages the coordinator delivers, runs decide,
  ingest, a policy's handlers and translating callbacks, and a projector,
  reports a stale command's conflicts, and turns whatever escapes business
  code into a key and its args. Admission, upcasting, snapshots, descriptor
  validation, policy output checks and conflict judgement are the
  coordinator's. Its complete contract is angzarr-project's
  `features/handler-contract/` tier, which the Rust router
  ([angzarr-router](https://github.com/angzarr-io/angzarr-router), the
  reference) runs too.
- **Coordinator clients** — `CommandHandlerClient`, `QueryClient`, `DomainClient`
  and the `CommandBuilder` /
  `QueryBuilder` fluent builders, for code that talks to a running
  coordinator.
- **`ComponentHost`** — a generic gRPC host for the registered components:
  the coordinator-facing framework services, health/readiness, transport
  from the environment, and a hook for the application's own services.
- **`angzarr_client.testing`** — test helpers (`make_cover`,
  `make_event_book`, `uuid_for`, `ScenarioContext`, …), imported from that
  module explicitly.

## Installing

Pure Python. Wheels carry the generated framework protos; installing from
git builds the wheel, generating them with grpcio-tools:

```bash
pip install "angzarr-client @ git+https://github.com/angzarr-io/angzarr-client-python@<commit>"
```

## Generating a component

Declare components in proto with the `(io.angzarr.v1.component)`,
`(io.angzarr.v1.command)` and `(io.angzarr.v1.event)` options (see
angzarr-project's `options.proto`), then scaffold the domain's project with
the [angzarr CLI](https://github.com/angzarr-io/angzarr-cli): `angzarr
scaffold python --domain <domain>` writes the domain's logic interface and
its implementation (yours), and regenerates its plain types (`domain.py`),
its dispatch for this package's router (`_gen`), its `ComponentDescriptor`
(`_descriptor.py`) and its child-process entry point (`main.py`). The CLI's
README says what each file holds. `just codegen-check` runs it against this
package.

Implement the stub and host it:

```python
from angzarr_client import ComponentHost, configure_logging
from myapp._version import __version__  # versionator output emit python > myapp/_version.py
from myapp._descriptor import DESCRIPTOR  # the ComponentDescriptor the CLI generated
from myapp._gen.my.v1.thing_aggregate_angzarr import new_thing_aggregate_dispatch
from myapp.logic_impl import ThingLogicImpl

configure_logging()
host = ComponentHost(rules_version=__version__)
host.add_aggregate(new_thing_aggregate_dispatch(ThingLogicImpl()), DESCRIPTOR)
# An application's own gRPC service, served and health-reported alongside:
# host.add_service(add_MyQueryServiceServicer_to_server, MyQueryServicer(), "my.v1.MyQueryService")
host.run()   # binds the transport the environment selects; stops on SIGTERM / SIGINT
```

A host serves exactly ONE component (one business process is one component):
`add_policy`, `add_process_manager` or `add_projector` instead of
`add_aggregate`, each with its generated descriptor. A host given a second
component refuses to start. An upcaster is its component's sidecar, a
process of its own (decision 14.39): a host given only `add_upcaster`
serves `UpcasterService` and health, and a host given a component and an
upcaster refuses to start. `rules_version` is the binary's full build
version: every hosted component reports it on every answer. A component's
host always serves `ComponentService.Describe`, answered with its generated
descriptor unchanged, which the coordinator calls to learn the component
it hosts and validates. The transport comes from the environment: `TRANSPORT_TYPE=tcp`
(default; `ANGZARR_BIND_ADDRESS`, else `[::]:$PORT`) or `TRANSPORT_TYPE=uds`
(`$UDS_BASE_PATH/$SERVICE_NAME[-<qualifier>].sock`, removed on shutdown).
Health reports `NOT_SERVING` until the server listens, and again once shutdown
begins; in-flight calls then finish within the
grace period. A coded failure from a handler travels as its gRPC status, with
a `google.rpc.Status` / `ErrorInfo` (reason = the error code) in
`grpc-status-details-bin`.

### Writing a component by hand

Everything the CLI generates can be replaced by hand. The dispatch table
is `angzarr_client.router`'s `AggregateDispatch`, `ProcessManagerDispatch`,
`PolicyDispatch` or `ProjectorDispatch`, filled in with its handlers;
handlers are ordinary synchronous functions (the host runs them on its
worker pool). The descriptor, which the CLI writes as a hex-encoded
protobuf in `_descriptor.py`, is built from the table by `describe`:

```python
from angzarr_client import ComponentHost, describe
from angzarr_client.router import PolicyDispatch, ProcessManagerDispatch, ProjectorDispatch, Rebuilder

# A projector over several source domains: one input per domain.
totals = (
    ProjectorDispatch("Totals", TotalsView)
    .for_domains("order", "refund")
    .on_event("orders.v1.OrderPlaced", add_order)
    .on_event("refunds.v1.Refunded", subtract_refund)
)
host = ComponentHost(rules_version=__version__)
host.add_projector(totals, describe(totals, rules_version=__version__))

# A policy: its one input domain and its one output domain.
reserve = PolicyDispatch("ReserveStock", "order", "inventory").on_event("orders.v1.OrderPlaced", reserve_items)
describe(reserve, rules_version=__version__)

# A process manager: a state, and the types that start an instance.
fulfil = ProcessManagerDispatch("Fulfilment", "fulfilment", Rebuilder(FulfilmentState)).on_command(...)
describe(fulfil, rules_version=__version__, starting_types=[StartFulfilment])
```

The `ComponentDescriptor` (angzarr-project `types.proto`) the coordinator
pulls with `ComponentService.Describe`, field by field, as `describe` fills
it from the table:

| field | set for | value |
|---|---|---|
| `name` | every kind | the table's `name` |
| `kind` | every kind | `COMPONENT_KIND_AGGREGATE`, `_PROCESS_MANAGER`, `_POLICY` or `_PROJECTOR` |
| `rules_version` | every kind, required | the build version: `rules_version=`, else the table's own; it must be the version every answer reports (the table's own, else `ComponentHost(rules_version=...)`), and the host refuses a descriptor of any other at once (`ConfigurationError`, code `DESCRIPTOR_RULES_VERSION_MISMATCH`, details `component`, `described`, `reported`) |
| `inputs` | policy, projector | the domains it subscribes to: a policy's one `input_domain`; a projector's source domains (`for_domains`; none given, the wildcard, names none) |
| `state.type_url` | aggregate, process manager, required | `"/"` and the state message's full name (`"/orders.v1.OrderState"`): `state_type=` (a message class or a full name), else the class the table's `Rebuilder` makes |
| `state.options.conflict_resolutions` | aggregate, process manager | the full resolved list: `STRICT` and `AGGREGATE_HANDLES`, which every state supports, plus `resolutions=` (else what the state message's `(io.angzarr.v1.state)` option lists; in practice `COMMUTATIVE`), deduplicated, in enum-number order: `[STRICT, AGGREGATE_HANDLES]` or `[COMMUTATIVE, STRICT, AGGREGATE_HANDLES]`. Never `UNSPECIFIED` |
| `starting_types` | process manager only | the full type NAMES, no type URL prefix (`"orders.v1.StartFulfilment"`), of the commands and facts that start an instance: `starting_types=` (message classes or names; a type URL is cut to its name). An input for a correlation with no instance whose type is not listed is not delivered |
| `output_domain` | policy only, required | the one domain its commands go to: the table's `output_domain` |

The coordinator refuses a descriptor that breaks one of these rules (in this
order, each its `CoordinatorReason` key): an empty `rules_version`
(`COORDINATOR_REASON_MISSING_RULES_VERSION`); `starting_types` on a kind
other than a process manager (`COORDINATOR_REASON_STARTING_TYPES_ON_NON_PM`);
no `state` on an aggregate or a process manager
(`COORDINATOR_REASON_MISSING_STATE_TYPE`), or a `state` on a policy or a
projector (`COORDINATOR_REASON_STATE_ON_STATELESS_KIND`); a policy without
exactly one input (`COORDINATOR_REASON_POLICY_INPUT_COUNT`) or without an
`output_domain` (`COORDINATOR_REASON_POLICY_NO_OUTPUT_DOMAIN`); an
`output_domain` on any other kind
(`COORDINATOR_REASON_OUTPUT_DOMAIN_ON_NON_POLICY`). A refused component is
never delivered to. `describe` never builds such a descriptor: it raises
`ConfigurationError` with that key as its code.

`describe` builds what the CLI generates: `just codegen-check` checks that
`describe` of each scaffolded component's dispatch equals its generated
`DESCRIPTOR`, and a test pins a generated descriptor's bytes against the
construction above.

A descriptor built any other way (by hand, as a `types_pb2.ComponentDescriptor`)
works the same: the host answers `Describe` with whatever it is given,
unchanged.

### As a child of an in-app runtime

angzarr-embedded (the Rust runtime that runs inside an application) launches
a Python component as a child process (by default `uv run --locked <entry
point>`) and calls it over gRPC on a Unix socket. The child calls
`run_child(host)` (`angzarr_client.child`) instead of `host.run()`:

- the socket is `$ANGZARR_SOCKET` (made absolute), else a fresh path in the
  system temporary directory;
- once the host is SERVING, the child writes exactly one line to stdout,
  `angzarr-handshake v1 unix:<absolute-socket-path>`, then closes stdout.
  Nothing else ever goes to stdout: logs and stray prints go to stderr. A
  child that exits without the line failed to start, and its stderr says why;
- it serves until stdin reaches end of file or it gets SIGTERM or SIGINT,
  then stops gracefully and removes the socket.

A parent parses the line with `parse_handshake`; `v1` is the format version.

## Coordinator clients

```python
from angzarr_client import DomainClient

client = DomainClient.connect("localhost:1310")
response = (
    client.command_handler
    .command("my-domain", root)
    .with_command("/my.v1.DoSomething", do_something)
    .with_expected_sequence(seen_next_sequence)   # optional Handling
    .execute()
)
events = client.query.query("my-domain", root).get_event_book()
workflow = client.get_correlated_events(correlation_id)
```

A command is sent in one of three modes, named after the coordinator RPCs:
`execute_async()` / `handle_async()` (HandleAsync: returns once accepted,
answers nothing), `execute()` / `handle()` (Handle: the decision) and
`execute_sync()` / `handle_sync()` (HandleSync: the decision plus the
synchronous projectors in `response.outcome`). A stale expected sequence fails
with an error whose `is_aborted()` is true.

Type URLs are `"/"` + the message's fully-qualified name (`TYPE_URL_PREFIX`);
any prefix is accepted on input, matching by the name after the last `/`.
`compute_root(domain, key)` derives an aggregate root as
`uuid5(ANGZARR_ROOT_NAMESPACE, f"{domain}:{key}")`, refusing a domain that
contains `:`.

## Development

Submodule: `angzarr-project` (the spec: protos, feature files and the
router tier's fixtures).

| recipe | does |
|---|---|
| `just proto` | generate the framework protos into `angzarr_client/proto/` (grpcio-tools; `scripts/proto_build.py`) |
| `just build` | sdist + pure-Python wheel (the wheel build runs `just proto`'s code), then install the wheel in a clean environment and check it loads |
| `just cli` | install the angzarr CLI at the pinned `CLI_REV` into `.tools/` with cargo (`ANGZARR_CLI=<binary>` uses another build) |
| `just contract-gen` | generate the handler-contract tier's fixture messages into `tests/router/gen/` |
| `just test` | `proto` and `contract-gen`, then unit, router (handler-contract tier) and client-feature tests |
| `just codegen-check` | scaffold angzarr-project's example Python domain (the player) with the CLI and check its dispatch imports, its host registers it and its descriptor loads |
| `just ci` | format check + lint + `test` + `codegen-check` (what CI runs) |
| `just clean` | remove generated protos and rendered code |

Tests:

- `tests/router/` — angzarr-project's `features/handler-contract/` tier,
  the router's complete contract, run in place against the router over the
  tier's fixtures (`test_handler_contract.py`, business code in
  `contract.py`), the router's unit tests and the host's.
- `tests/client/` — angzarr-project's `features/client` client-surface tier and
  `parity/client` against the coordinator clients and a test backend.
- `tests/test_*.py` — unit tests.

## License

AGPL-3.0 — see [LICENSE](LICENSE).
