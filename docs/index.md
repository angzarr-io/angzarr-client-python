# angzarr-client — Python reference

API reference for the Python client of the Angzarr CQRS/ES framework.
The reference pages under [API Reference](autoapi/angzarr_client/index)
are generated from the package's docstrings.

For narrative documentation (getting started, concepts, patterns,
cross-language comparisons), see [angzarr.io](https://angzarr.io). The
project README in this repo shows how to generate and host a component.

## Installation

```bash
pip install angzarr-client
```

## Where to start in the reference

- **Clients** — `DomainClient`, `CommandHandlerClient`, `QueryClient`
- **Router binding** — `angzarr_client.router`: `Router`, the `*Dispatch` tables the generated wiring populates, `CommandContext`, `PageContext`, `FactRecord`, `CodedError`
- **Descriptors** — `angzarr_client.descriptor`: `describe`, a hand-written component's `ComponentDescriptor` from its dispatch table; its module docs (and the README's "Writing a component by hand") give the descriptor's fields and the coordinator's rules
- **Errors** — `ClientError` hierarchy with `is_*` predicate methods
- **Retry** — `ExponentialBackoffRetry` with `execute()` and `on_retry` callback
- **Testing helpers** — `ScenarioContext`, `make_cover`, `make_event_book`, `uuid_for`, …

```{toctree}
:maxdepth: 2
:hidden:

autoapi/angzarr_client/index
```
