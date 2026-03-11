# Tasks - angzarr-client-python

## In Progress

## To Do

- [ ] Buf Registry Setup: Create buf.build account, publish protos to buf.build/angzarr/angzarr
- [ ] PyPI Publishing: Configure PYPI_TOKEN secret, create release workflow
- [ ] CI/CD Setup: Create .github/workflows/ci.yml for test runs (Python 3.10+)

## Backlog

- [ ] Feature File Syncing: Decide if features should be published as separate package
- [ ] Documentation: Add API reference generation (Sphinx or MkDocs)
- [ ] Pre-existing Test Failures: Fix saga validation tests (2 failures, non-blocking)

## Done

- [x] Proto API changes: PageHeader, angzarr_deferred structure complete
- [x] CompensationContext updated for new RejectionNotification structure
- [x] All sequence field access fixed (page.header.sequence)
- [x] Cache key format updated (edition:domain:root)
- [x] 695/697 tests passing
