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
- **`codegen/`** — the templates `angzarr codegen python` / `angzarr scaffold
  python` render from your proto component declarations: a typed
  `<Component>Handler` protocol plus the dispatch wiring (regenerated), and a
  handler stub (written once).
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
angzarr-project's `options.proto`), then render this repository's templates
with the [angzarr CLI](https://github.com/angzarr-io/angzarr-cli):

```yaml
# buf.gen.yaml
version: v2
managed:
  enabled: true          # the CLI needs a go_package on every file
plugins:
  - remote: buf.build/protocolbuffers/python:v35.1
    out: gen
  - local: ["angzarr", "codegen", "python"]
    out: gen
    opt:
      - paths=source_relative
      - templates=github.com/angzarr-io/angzarr-client-python@<commit>
    strategy: all
  - local: ["angzarr", "scaffold", "python"]
    out: src
    opt:
      - paths=source_relative
      - out_dir=src
      - templates=github.com/angzarr-io/angzarr-client-python@<commit>
    strategy: all
```

The wiring imports the router binding as `angzarr_client.router` and the
framework protos from `angzarr_client.proto`; the template parameters
`param.runtime_module=` and `param.framework_package=` override either
(`codegen/manifest.yaml`). The template contract is angzarr-cli's
`docs/templates.md`.

Implement the stub and host it:

```python
from angzarr_client import ComponentHost, configure_logging
from myapp._version import __version__  # versionator output emit python > myapp/_version.py
from myapp.gen.my.v1.thing_aggregate_angzarr import new_thing_aggregate_dispatch
from myapp.thing_aggregate_angzarr_handler import ThingAggregate

configure_logging()
host = ComponentHost(rules_version=__version__)
host.add_aggregate(new_thing_aggregate_dispatch(ThingAggregate()))
# An application's own gRPC service, served and health-reported alongside:
# host.add_service(add_MyQueryServiceServicer_to_server, MyQueryServicer(), "my.v1.MyQueryService")
host.run()   # binds the transport the environment selects; stops on SIGTERM / SIGINT
```

A host serves exactly ONE component (one business process is one component):
`add_saga`, `add_process_manager` or `add_projector` instead of
`add_aggregate`, and `add_upcaster` beside it (an upcaster is part of its
component). A host given a second component refuses to start. `rules_version` is the binary's full build version: every hosted
component reports it on every answer, and the host refuses to start without
it. The host always serves `ComponentService.Describe`, which the coordinator
calls to learn the components it hosts. The transport comes from the environment: `TRANSPORT_TYPE=tcp`
(default; `ANGZARR_BIND_ADDRESS`, else `[::]:$PORT`) or `TRANSPORT_TYPE=uds`
(`$UDS_BASE_PATH/$SERVICE_NAME[-<qualifier>].sock`, removed on shutdown).
Health reports `NOT_SERVING` until the server listens, and again once shutdown
begins; in-flight calls then finish within the
grace period. A coded failure from a handler travels as its gRPC status, with
a `google.rpc.Status` / `ErrorInfo` (reason = the error code) in
`grpc-status-details-bin`.

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
| `just cli` | install the angzarr CLI at the pinned `CLI_REV` into `.tools/` (`ANGZARR_CLI=<binary>` uses another build) |
| `just contract-gen` | generate the handler-contract tier's fixture messages into `tests/router/gen/` |
| `just test` | `proto` and `contract-gen`, then unit, router (handler-contract tier) and client-feature tests |
| `just codegen-check` | render angzarr-project's example protos with `codegen/` and check every component imports, its stub implements its protocol, and it registers on the router |
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
