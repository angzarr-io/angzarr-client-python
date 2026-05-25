# WIP Skiplist Cleanup — Phase Plan

Clear the maintained `WIP_SCENARIOS` skiplists across `client-python` and
`examples-python`. Both skiplists were populated post the cucumber
business-vocabulary rewrite (angzarr-project commits 16fa309 + 20d53e6)
to suppress scenarios whose step matchers were left as `raise
NotImplementedError("WIP: step needs implementation")` stubs.

The goal of this plan is not "implement new features"; it is "wire
already-existing matcher idioms / domain logic to the new vocabulary the
spec uses." Triage confirmed ~85% of client-python stubs are
business-vocab rephrasings of a sibling matcher already implemented in
the same file. Examples-python has a comparable structure once you
account for the hand domain having ~2,600 lines of existing handler
code (the stubs wire into it, they don't build it).

## Scope

**In scope — Phase 1 (client-python):**
- Clear all 114 entries in `tests/client/wip_scenarios.py`.
- Implement all 171 stub matchers across 13 step files.
- No proto / router / dispatch surface changes expected (one possible
  exception in `edition_propagation` — see R9 risks).

**In scope — Phase 2 (examples-python), sketched at end:**
- Clear the tractable subset of `wip_scenarios.py` (167 entries) via
  wiring into existing handler implementations.

**Out of scope:**
- Adding *new* business behavior (this is wiring, not feature work).
- Touching `.feature` files in `vendor/angzarr-project` (the spec is the
  source of truth; the harness adapts to it).
- Changes to the router / dispatch / proto surfaces beyond what an
  already-merged commit (e.g. ccb8908 Cover.ext propagation) made
  available.

## Approach

Per-file rounds. Each round:
1. Read the file's stub section (bottom of file, marked with a comment
   banner `# WIP — Batch N/M new business-vocab phrasing`).
2. For each stub: find the sibling matcher above it that targets a
   near-identical action; copy the body, rename parameters as needed.
   For stubs without a sibling: build from `World` state + feature-file
   intent.
3. Where two stubs would have identical bodies and differ only in the
   decorator's regex: **add the new decorator to the existing matcher
   and delete the stub** rather than duplicate the body. Keeps step
   impls DRY.
4. After all stubs in the file: run the file's pytest, observe which
   previously-skipped scenarios now pass, remove those entries from
   `WIP_SCENARIOS`. Leave scenarios whose stubs live in other files.
5. Single commit, focused message.

## Branch strategy

Branch `test/clear-wip-skiplist` off the current
`feat/python-rust-parity-cleanup`.

After all 13 rounds green, open one PR. Conflicts with the feature
branch are unlikely (this plan only touches `tests/client/` and
`tests/client/steps/`).

## Doneness gate per round

1. Every `NotImplementedError("WIP: step needs implementation")` in the
   round's file is replaced with a real body, OR the stub function is
   deleted in favor of decorator-stacking on the sibling impl.
2. `uv run pytest tests/client/steps/test_<file>.py -v` is green.
3. `just test` is green end-to-end: 0 regressions in `tests/router/`
   and `tests/test_*`. `skipped` count strictly decreases.
4. `WIP_SCENARIOS` entries for scenarios that now pass are removed.
5. `just fmt` clean (ruff + black).
6. Pre-commit hook (lefthook fmt + test) passes naturally — no
   `--no-verify`.
7. Single commit, message of form
   `test(cucumber): un-stub <file>.py matchers (<N> scenarios green)`.

## Rounds

Ordering rule: fastest wins first (build cadence + confidence in the
template), edition_propagation last (the only round that may need new
`World` plumbing).

### R0 — Setup
- Branch `test/clear-wip-skiplist` from current HEAD.
- Read `tests/client/conftest.py` — confirm `World` field set and
  shared `@then` matchers.
- Skim each step file's stubs section, note any stub that looks like
  it needs cross-file shared state. If found, extend `World` in this
  round, not later.
- Baseline: `just test` green; capture skip count (=114).

### R1 — command_handler.py (20 stubs)
First because: stubs at lines 353/361/367 (`_then_book_ext_*`) are now
1-2 line assertions, unblocked by ccb8908 (Cover.ext propagation).
Captures momentum + validates the round template against the simplest
possible un-stubs.

### R2 — Tiny files combined (7 stubs)
Bundle `projector.py` (2) + `speculative_client.py` (2) +
`multi_handler.py` (3) in one round. Three files, one commit. Validates
the cross-file flow.

### R3 — process_manager.py + rejection.py (14 stubs)

### R4 — domain_client.py + rejected_compensation.py (21 stubs)

### R5 — builder.py (14 stubs)

### R6 — query_client.py (15 stubs)

### R7 — compensation.py (22 stubs)

### R8 — aggregate_client.py (39 stubs)
Largest file. Pre-flight: estimate body count and decide whether to
split into R8a/R8b (target ≤25 stubs per commit for review-ability).

### R9 — edition_propagation.py (19 stubs) — DELETED, not deferred
Outcome: the file's scaffold was removed entirely. The
edition-propagation contract is coordinator-tier (Rust sidecar) —
implemented in `core/main/src/orchestration/process_manager/edition_propagation.rs`
and `core/main/src/orchestration/saga/grpc/mod.rs:99-116`, with
authoritative unit-test coverage in
`process_manager/edition_propagation.test.rs`. The Python router
dispatch path explicitly does not implement it (see
`dispatch.py:644-645`, audit-#86 reversion).

The Python client doesn't own coverage for coordinator-tier contracts,
so the scaffold at `tests/client/steps/test_edition_propagation.py`
was removed; the 8 `edition_propagation.feature` WIP entries were
dropped from `WIP_SCENARIOS`. The feature file remains in the spec
repo (`vendor/angzarr-project/features/coordinator-contract/`) but is
never collected by Python pytest.

## Per-stub workflow

For each stub:
1. Read the stub's `@given`/`@when`/`@then` decorator and signature.
2. `grep -n "@\(given\|when\|then\)" <file>` to find the sibling
   matcher 50-200 lines above it.
3. Decide: **decorator-stack** (add the new regex to the existing
   matcher, delete the stub) or **body-copy** (parameters differ, copy
   the body with renames).
4. Implement.
5. Move on. Don't rerun the suite after each stub — wait until the
   file is done.

After all stubs in the file are written:
1. `uv run pytest tests/client/steps/test_<file>.py -v` — observe pass
   count.
2. `git diff tests/client/wip_scenarios.py` (won't be touched yet).
3. Re-run the suite: scan output for `SKIPPED` entries that came from
   the round's file's scenarios. Remove those tuples from
   `WIP_SCENARIOS`.
4. `just test` end-to-end. Confirm skip count dropped by the expected
   N.
5. Commit.

## Risks + mitigations

1. **Stub needs new `World` plumbing.** Pause the round, add the field
   to `World` in `conftest.py`, update any neighboring matchers that
   would naturally populate it, continue. Don't refactor speculatively.

2. **A scenario's stubs span multiple files.** A scenario is only
   un-skipped once *all* its stubs are real. The mechanical loop above
   handles this: each round un-skips scenarios that become green; the
   remainder waits for its other rounds.

3. **edition_propagation balloons beyond ~4 hr.** Time-box it. If the
   work is genuinely deeper than the triage suggested, ship rounds R1–R8
   green and split R9 into its own follow-up PR with a design note.

4. **Lefthook full-suite hook slows commits.** Currently ~5.6 s. After
   un-skipping, ~10 s. Acceptable. If not, swap pre-commit to
   `just test-pytest` (skip cucumber) for the duration of the cleanup.

5. **Merge with parallel work on `feat/python-rust-parity-cleanup`.**
   This branch only touches `tests/client/`. Cross-file conflicts
   unlikely. Resolve by rebasing R-by-R.

6. **Decorator-stacking pattern surprises pytest-bdd.** pytest-bdd
   supports multiple decorators on one function. Verify on R1; if it
   misbehaves, fall back to body-copy for the whole plan.

## Parallelization (optional)

The per-file structure is embarrassingly parallel modulo merge conflicts
on `wip_scenarios.py`. If wall-clock matters more than reviewability:
- Spawn N subagents, one per file.
- Each agent commits its step-file edits only (no `wip_scenarios.py`
  touches).
- A single closer pass collects all green scenarios and updates
  `WIP_SCENARIOS` in one final commit.

Trade-off: 12–18 hr sequential → ~3 hr wall with 6 agents, at the cost
of reviewability and the loss of round-by-round confidence checks.
**Default: sequential.** Switch only if explicitly requested.

## Phase 2 — examples-python sketch

Pre-flight: re-triage hand domain depth. The original WIP triage assumed
`hand_steps.py`'s 709 `pass`-bodied stubs needed a poker engine built
from scratch. Filesystem check shows `hand/agg/handlers/` already has
2,637 lines (`game_rules.py` 848 + `hand.py` 1,783). The work is
near-certainly wiring stubs into the existing handlers, not building
the engine. Real estimate emerges after the re-triage.

Round structure (refined after Phase 1 + re-triage):

**E1 — process_manager_steps.py Then-side** (~30 min): 3 scenarios at
lines 1119/1131/1140/1151 — assertions against already-populated
`context.bi_event`.

**E2 — tournament_steps.py lone stub** (~10 min): 1 of 208 matchers,
1 scenario.

**E3 — table.feature business-vocab aliasing** (~1–2 d, 83 scenarios):
add `@when`/`@then` regex aliases on existing fully-implemented matchers
(per triage, the impls exist; only the new vocab regexes are missing).

**E4 — hand domain wiring** (estimate TBD after re-triage; provisionally
3–5 d not 3–6 weeks): wire the 709 `hand_steps.py` stubs to the
existing `hand/agg/handlers/` implementations. Per the user's prior, the
stubs are call-sites missing into real handlers, not undefined business
logic. Re-triage should confirm this before E4 work starts.

**E5 — saga.feature 6 WIP** (provisional, ~1 d): likely existing saga
matchers needing vocab aliases per the E3 pattern.

## Out-of-scope deferred

- Re-running the un-skipped client-python scenarios under the gRPC
  integration boundary. Those live in examples-python's cucumber
  acceptance suite and are addressed by the existing kind cluster
  workflow.
- Auditing the `WIP_SCENARIOS` file header comment. Once empty, the
  file should be deleted along with the `pytest_collection_modifyitems`
  hook in `conftest.py` — drop both in a final cleanup commit.
- Anything related to the `pmg-reservation-pm` CrashLoopBackOff or the
  `poker-angzarr-status` ImagePullBackOff observed during the kind
  survey — those are coordinator / image issues outside this plan.
