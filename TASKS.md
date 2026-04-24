# Tasks - angzarr-client-python

## In Progress

## To Do

- [ ] Buf Registry Setup: Create buf.build account, publish protos to buf.build/angzarr/angzarr
- [ ] PyPI Publishing: Configure PYPI_TOKEN secret, create release workflow

## Backlog

- [ ] Documentation: Add API reference generation (Sphinx or MkDocs)

## Done

- [x] Proto API changes: PageHeader, angzarr_deferred structure complete
- [x] CompensationContext updated for new RejectionNotification structure
- [x] All sequence field access fixed (page.header.sequence)
- [x] Cache key format updated (edition:domain:root)
- [x] 921/921 tests passing (Phase 3 step-def ports merged — see PR #6)
- [x] CI/CD: .github/workflows/ci.yml (lint + test matrix 3.10/3.11/3.12 + build + notify-downstream) and .github/workflows/mutation.yml (weekly)
- [x] Phase 1–7 cross-language parity with angzarr-client-rust (see merged PRs)
- [x] Feature File Syncing: resolved via git submodule. `angzarr-project` is tracked as a submodule at the repo root; feature files live at `angzarr-project/features/client/*.feature` and are loaded by the step-defs directly. Same mechanism used by angzarr-client-rust. No separate package needed.
