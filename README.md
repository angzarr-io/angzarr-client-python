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
- **`angzarr_client.testing`** — test helpers (`make_cover`,
  `make_event_book`, `uuid_for`, `ScenarioContext`, …), imported from that
  module explicitly.

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

Implement the stub, register it, and dispatch:

```python
from angzarr_client.router import Router
from myapp.gen.orders.v1.order_aggregate_angzarr import register_order_aggregate
from myapp.order_aggregate_angzarr_handler import OrderAggregate

router = Router()
register_order_aggregate(router, OrderAggregate())
response = router.dispatch(contextual_command)   # a BusinessResponse
```

## Coordinator clients

```python
from angzarr_client import DomainClient

client = DomainClient.connect("localhost:1310")
response = (
    client.command_handler
    .command("orders", order_root)
    .with_command("/orders.v1.CreateOrder", create_order)
    .execute()
)
events = client.query.query("orders", order_root).get_event_book()
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
| `just proto` | generate the framework + ABI protos into `angzarr_client/proto/` |
| `just router-lib` | build the router-ffi cdylib from `angzarr-router/` (cargo, protoc) and vendor it into `angzarr_client/router/_lib/` |
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
