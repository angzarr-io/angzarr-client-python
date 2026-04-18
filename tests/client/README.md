# tests/client — Python step defs for unit-client tier

Implements the scenarios in
[`angzarr-project/features/client/`](../../angzarr-project/features/client/)
using **pytest-bdd** (not behave — pytest-bdd integrates with the existing
pytest suite).

## What's here

- `conftest.py` — `World` dataclass + `world` fixture; a few cross-cutting
  `@then` steps used by multiple feature files
- `steps/_helpers.py` — constructors for `ContextualCommand`, `EventBook`,
  `CommandBook`, `Notification`, `SagaHandleRequest`, `PMHandleRequest`
- `steps/test_*.py` — one per feature file, each starting with
  `scenarios("<file>.feature")` and defining `@given/@when/@then` steps for
  that feature

## Style

Synchronous, direct-state, generic `Order`/`Payment`/`Inventory` domains.

- State is a plain `@dataclass` constructed in `@given` steps
- Handler classes are defined inline in step fixtures (one-off, per scenario)
- `Router(...).with_handler(cls, lambda: cls()).build()` is the whole setup
- Assertions inspect the returned `BusinessResponse` / `SagaResponse` /
  `PMHandleResponse` / `Projection`
- No sidecars, no gRPC, no `within N seconds`, no poker types

## Wiring

pytest-bdd's feature lookup is configured in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
bdd_features_base_dir = "angzarr-project/features/client"
```

Each `scenarios("command_handler.feature")` call resolves to
`angzarr-project/features/client/command_handler.feature`. No symlinks, no
copies — pytest-bdd reads directly from the git submodule mount.

## Running

```bash
# From client-python/main/

# Framework-harness cucumber only
just test-client-unit

# Plain pytest (router internals, helpers, errors)
just test-pytest

# Both
just test
```

## Adding a scenario

1. **First**: add the scenario in
   [`angzarr-project/features/client/<file>.feature`](../../angzarr-project/features/client/),
   allocate the next `@C-NNNN` ID (see [the tier README](../../angzarr-project/features/client/README.md))
2. Land the angzarr-project PR
3. Bump the submodule pointer here (`git -C angzarr-project checkout <sha>`)
4. Add step defs in the matching `steps/test_<file>.py`
5. Verify: `just test-client-unit`

Between steps 2 and 4, `just test-client-unit` reports the new scenario as
missing step definitions — this is by design (see the three-tier model's
[root README](../../angzarr-project/features/README.md)).
