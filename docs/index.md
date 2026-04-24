# angzarr-client — Python reference

API reference for the Python client of the Angzarr CQRS/ES framework.
The reference pages under [API Reference](autoapi/angzarr_client/index)
are generated from the package's docstrings.

For narrative documentation (getting started, concepts, patterns,
cross-language comparisons), see [angzarr.io](https://angzarr.io). The
project README in this repo has the quick-start example and a
"Coming from Rust?" table for readers moving between languages.

## Installation

```bash
pip install angzarr-client
```

## Where to start in the reference

- **Clients** — `DomainClient`, `CommandHandlerClient`, `QueryClient`, `SpeculativeClient`
- **Router + handler kinds** — `Router`, `@command_handler`, `@saga`, `@process_manager`, `@projector`, `@upcaster`
- **Errors** — `ClientError` hierarchy with `is_*` predicate methods
- **Retry** — `ExponentialBackoffRetry` with `execute()` and `on_retry` callback
- **Testing helpers** — `ScenarioContext`, `make_cover`, `make_event_book`, `uuid_for`, …

```{toctree}
:maxdepth: 2
:hidden:

autoapi/angzarr_client/index
```
