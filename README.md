# angzarr-client (Python)

The home of [Angzarr](https://angzarr.io) for Python:

- **`angzarr_client.router`** — the router binding. Components (aggregates,
  sagas, process managers, projectors) are hosted by the shared Rust router
  ([angzarr-router](https://github.com/angzarr-io/angzarr-router)'s
  `router-ffi` cdylib), loaded in-process through cffi. Dispatch, state
  rebuild, rejection and compensation routing run in the router; Python holds
  the business handlers.
- **`codegen/`** — the templates `angzarr codegen python` / `angzarr scaffold
  python` render from your proto component declarations: a typed
  `<Component>Handler` protocol plus the dispatch wiring (regenerated), and a
  handler stub (written once).
- **Coordinator clients** — `CommandHandlerClient`, `QueryClient`,
  `SpeculativeClient`, `DomainClient` and the `CommandBuilder` /
  `QueryBuilder` fluent builders, for code that talks to a running
  coordinator.
- **`ComponentHost`** — a generic gRPC host for the registered components:
  the coordinator-facing framework services, health/readiness, transport
  from the environment, and a hook for the application's own services.
- **`angzarr_client.testing`** — test helpers (`make_cover`,
  `make_event_book`, `uuid_for`, `ScenarioContext`, …), imported from that
  module explicitly.

## Installing

Linux x86_64 only. Wheels are tagged `manylinux_2_<N>_x86_64` and carry the
generated framework protos and the router-ffi library. Installing from git
builds the wheel, which needs a Rust toolchain (`cargo`) on `PATH`:

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
from myapp.gen.my.v1.thing_aggregate_angzarr import new_thing_aggregate_dispatch
from myapp.thing_aggregate_angzarr_handler import ThingAggregate

configure_logging()
host = ComponentHost()
host.add_aggregate(new_thing_aggregate_dispatch(ThingAggregate()))
# An application's own gRPC service, served and health-reported alongside:
# host.add_service(add_MyQueryServiceServicer_to_server, MyQueryServicer(), "my.v1.MyQueryService")
host.run()   # binds the transport the environment selects; stops on SIGTERM / SIGINT
```

`add_saga`, `add_process_manager`, `add_projector` and `add_upcaster` serve the
other kinds. The transport comes from the environment: `TRANSPORT_TYPE=tcp`
(default; `ANGZARR_BIND_ADDRESS`, else `[::]:$PORT`) or `TRANSPORT_TYPE=uds`
(`$UDS_BASE_PATH/$SERVICE_NAME[-<qualifier>].sock`, removed on shutdown).
Health reports `NOT_SERVING` until the server listens and the readiness probes
pass, and again once shutdown begins; in-flight calls then finish within the
grace period. A coded failure from a handler travels as its gRPC status, with
a `google.rpc.Status` / `ErrorInfo` (reason = the error code) in
`grpc-status-details-bin`; the coordinator hands that code to compensation
handlers as `RejectionNotification.code` (branch on it, never on
`rejection_reason`, which is the human-readable message).

`ProjectorService.HandleSpeculative` folds the book speculatively: projector
handlers see `ctx.speculative` (`current_page().speculative` in the finisher)
and must then leave durable and external state (read models, stores,
outgoing messages) untouched — the projection is returned, not applied.

## Coordinator clients

```python
from angzarr_client import DomainClient

client = DomainClient.connect("localhost:1310")
response = (
    client.command_handler
    .command("my-domain", root)
    .with_command("/my.v1.DoSomething", do_something)
    .execute()
)
events = client.query.query("my-domain", root).get_event_book()
```

Type URLs are `"/"` + the message's fully-qualified name (`TYPE_URL_PREFIX`);
any prefix is accepted on input, matching by the name after the last `/`.
`compute_root(domain, key)` derives an aggregate root as
`uuid5(NAMESPACE_OID, f"{domain}:{key}")`.

## Development

Submodules: `angzarr-project` (the spec: protos and feature files) and
`angzarr-router` (the router at a pinned revision: the cdylib source, its ABI
protos and the conformance suite). Initialise both without `--recursive`.

| recipe | does |
|---|---|
| `just proto` | generate the framework + ABI protos into `angzarr_client/proto/` (grpcio-tools) |
| `just router-lib` | build the router-ffi cdylib from `angzarr-router/` (cargo) and vendor it into `angzarr_client/router/_lib/` |
| `just build` | sdist + manylinux wheel (the wheel build runs both of the above; `scripts/native_build.py`), then install the wheel in a clean environment and check it loads |
| `just cli` | install the angzarr CLI at the pinned `CLI_REV` into `.tools/` (`ANGZARR_CLI=<binary>` uses another build) |
| `just conformance-gen` | render the router's conformance fixture with `codegen/` into `tests/router/gen/` |
| `just test` | all of the above, then unit, binding, router-conformance and client-feature tests |
| `just codegen-check` | render angzarr-project's example protos with `codegen/` and check every component imports, its stub implements its protocol, and it registers on the router |
| `just ci` | format check + lint + `test` + `codegen-check` (what CI runs) |
| `just clean` | remove generated protos, the router build, rendered code |

Tests:

- `tests/router/` — the binding's unit tests and angzarr-router's
  `conformance/features`, run against the binding with handlers implementing
  the generated protocols (`tests/router/fixture.py`).
- `tests/client/` — angzarr-project's `features/client` client-surface tier and
  `parity/client` against the coordinator clients and a test backend.
- `tests/test_*.py` — unit tests.

## License

AGPL-3.0 — see [LICENSE](LICENSE).
