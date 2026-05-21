# angzarr-client-python commands

set shell := ["bash", "-c"]

# Reusable submodule-protection recipes (install-submodule-hooks,
# check-submodules-clean). Source of truth lives in
# vendor/angzarr-project/submodule.just; bumped via the standard bumper tooling.
import? 'vendor/angzarr-project/submodule.just'

ROOT := `git rev-parse --show-toplevel`
# Devcontainer image published by .github/workflows/container.yml from
# .devcontainer/Containerfile (ships uv + just + buf + python).
MUTATE_IMAGE := "ghcr.io/angzarr-io/angzarr-client-python:latest"

default:
    @just --list

# Run a mutation-testing target with the workspace mounted READ-ONLY.
#
# WHY:
#   mutmut copies sources into ``mutants/`` and mutates them in-place. If
#   the workspace is mounted RW and the container dies mid-run, the mutated
#   copy is left on the host. This helper closes that hole: source is
#   mounted at /src:ro, the workspace is tar'd into /work inside the
#   container's WRITABLE OVERLAY LAYER, and ``--rm`` destroys the overlay
#   (and the mutated copy) on every exit.
#
# WHAT TOUCHES THE HOST:
#   - {{ROOT}}/.mutants-cache/uv-cache — uv's package cache only. NEVER
#     contains mutated source. Gitignored. Delete to purge.
#   - {{ROOT}}/mutants/mutmut-cicd-stats.json (and mutmut-stats.json if
#     produced) — copied out at the end of a successful run so existing
#     tooling that reads them keeps working.
#
# WHAT NEVER TOUCHES THE HOST:
#   - The ``mutants/`` workdir with copied/mutated source.
#   - mutmut's intermediate caches inside the workspace copy.
[private]
_container-ephemeral +ARGS:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ "${DEVCONTAINER:-}" = "true" ]; then
        # Already inside a devcontainer — that container IS the ephemeral
        # boundary. Run directly; the outer just wrapper ensures --rm.
        just --justfile "{{ROOT}}/justfile.container" {{ARGS}}
        exit 0
    fi
    mkdir -p "{{ROOT}}/mutants" \
             "{{ROOT}}/.mutants-cache/uv-cache"
    docker run --rm --network=host \
        -u "$(id -u):$(id -g)" \
        -v "{{ROOT}}:/src:ro" \
        -v "{{ROOT}}/mutants:/out" \
        -v "{{ROOT}}/.mutants-cache/uv-cache:/uv-cache" \
        -v "{{ROOT}}/justfile.container:/etc/angzarr-justfile:ro" \
        -e UV_CACHE_DIR=/uv-cache \
        -e MUTANTS_EPHEMERAL=1 \
        -w /work \
        {{MUTATE_IMAGE}} bash -eu -o pipefail -c '
            echo "[ephemeral] copying /src -> /work (container overlay)"
            mkdir -p /work
            # tar|tar: rsync may not be present in the base image. Excludes
            # mirror what would normally be skipped — venv, prior mutmut
            # workdir, pytest cache, build artifacts, the uv cache that is
            # mounted separately, and the mutants cache itself.
            tar -C /src \
                --exclude=./.venv \
                --exclude=./.uv-cache \
                --exclude=./.pytest_cache \
                --exclude=./.mutants-cache \
                --exclude=./mutants \
                --exclude=./__pycache__ \
                -cf - . \
                | tar -C /work -xf -
            # Mount the container-side justfile into the copy so `just`
            # resolves the inner recipes (the original /src is RO).
            cp /etc/angzarr-justfile /work/justfile
            cd /work
            just {{ARGS}}
            # Persist ONLY the stats json(s) back to host. Mutated source
            # trees and intermediate working dirs die with the container.
            if [ -f /work/mutants/mutmut-cicd-stats.json ]; then
                cp /work/mutants/mutmut-cicd-stats.json /out/mutmut-cicd-stats.json
                echo "[ephemeral] mutmut-cicd-stats.json copied to host mutants/"
            fi
            if [ -f /work/mutants/mutmut-stats.json ]; then
                cp /work/mutants/mutmut-stats.json /out/mutmut-stats.json
            fi
        '

# Purge the local mutation build cache (.mutants-cache/) — uv package
# cache only; never holds mutated source.
mutants-purge-cache:
    rm -rf "{{ROOT}}/.mutants-cache"
    @echo "Removed {{ROOT}}/.mutants-cache"

# =============================================================================
# Proto generation — cross-language model (project_proto_generation_model)
# =============================================================================
# `.proto` sources live in the angzarr-project submodule. Bindings are NEVER
# committed (see .gitignore: angzarr_client/proto/**/*_pb2*.py). They are
# regenerated:
#   1. on `post-checkout` via lefthook (covers fresh clones, branch switch,
#      submodule bumps)
#   2. transparently as a recipe dependency of `build`, `test`, `lint`, etc.
#      The recipe is idempotent — mtime guard skips when bindings are newer
#      than the newest .proto source.
#
# Runs in the same devcontainer image used for test/mutation so the buf +
# uv toolchain is fixed (no host fallback). Rootless docker → `-u 0:0` per
# feedback_docker_rootless.

PROTO_SRC_DIR := ROOT + "/vendor/angzarr-project/proto"
PROTO_OUT_DIR := ROOT + "/angzarr_client/proto"

# Public entry point. Idempotent: returns immediately if bindings are
# fresher than the newest .proto source.
generate-proto:
    #!/usr/bin/env bash
    set -euo pipefail
    src_dir="{{PROTO_SRC_DIR}}"
    out_dir="{{PROTO_OUT_DIR}}"
    if [ ! -d "$src_dir" ]; then
        echo "[generate-proto] $src_dir missing — is the angzarr-project submodule initialized?" >&2
        exit 1
    fi
    # Staleness check: regenerate if any .proto file is newer than the
    # OLDEST generated binding, or if the bindings dir is empty.
    # Note: this catches "submodule bumped" and "fresh clone" cases (the
    # hot paths driving the lefthook trigger). It does NOT catch a user
    # who manually deletes one binding while leaving others fresh — that
    # case requires `just generate-proto-force`. A binding-count check
    # against the .proto count is unreliable because buf imports transitive
    # deps from BSR that have no .proto in src_dir.
    newest_proto=$(find "$src_dir" -name '*.proto' -printf '%T@\n' 2>/dev/null \
                    | sort -n | tail -1)
    if [ -d "$out_dir" ]; then
        oldest_pb=$(find "$out_dir" \( -name '*_pb2.py' -o -name '*_pb2_grpc.py' \) \
                        -printf '%T@\n' 2>/dev/null | sort -n | head -1)
    else
        oldest_pb=""
    fi
    if [ -n "$newest_proto" ] && [ -n "$oldest_pb" ] \
        && awk -v p="$newest_proto" -v b="$oldest_pb" 'BEGIN{exit !(b>p)}'; then
        echo "[generate-proto] bindings up-to-date, skipping (use 'just generate-proto-force' to override)"
        exit 0
    fi
    just generate-proto-force

# Always regenerate, ignoring mtimes. Invoked by `generate-proto` when stale
# and exposed directly for users who want to force a rebuild.
generate-proto-force:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ "${DEVCONTAINER:-}" = "true" ]; then
        # Inside the devcontainer image already — run directly.
        buf generate
        uv run --extra dev python scripts/generate_protos.py
        exit 0
    fi
    # Rootless docker: -u 0:0 maps to host user via subuid; writes to the
    # bind-mount land owned by the host user. See feedback_docker_rootless.
    docker run --rm --network=host \
        -u 0:0 \
        -v "{{ROOT}}:/work" \
        -w /work \
        -e DEVCONTAINER=true \
        {{MUTATE_IMAGE}} \
        bash -eu -o pipefail -c 'just generate-proto-force'

# Legacy alias — kept so existing recipe-deps and muscle memory keep working.
proto: generate-proto

# Framework-harness cucumber (pytest-bdd; feature files from vendor/angzarr-project/features/client/)
test-client-unit: generate-proto
    uv run --extra dev pytest tests/client/ -v

# Plain pytest, excluding the BDD subset
test-pytest: generate-proto
    uv run --extra dev pytest tests/ -v --ignore=tests/client

# Full suite
test: test-pytest test-client-unit

# Full suite with verbose output (matches Rust's `just test-verbose`)
test-verbose: generate-proto
    uv run --extra dev pytest tests/ -v -s

# Lint only (ruff). `fmt` already runs black --check + ruff check; this is
# a cross-language alias matching Rust's `just lint` = `cargo clippy -D warnings`.
lint: generate-proto
    uv run ruff check .

# Run tests with coverage
coverage: generate-proto
    uv run --extra dev pytest tests/ --cov=angzarr_client --cov-report=term-missing --cov-report=html

# Build Sphinx HTML docs into docs/_build/html.
# --keep-going finishes generation even when some xrefs are ambiguous
# (autoapi emits cross-references for TypeVars like `T` that appear in
# multiple modules; those warnings are cosmetic in the rendered output).
docs:
    uv run --extra docs sphinx-build -b html --keep-going docs docs/_build/html

# Serve the built docs locally
docs-serve:
    @echo "Open http://localhost:8000 — Ctrl-C to stop"
    python3 -m http.server --directory docs/_build/html 8000

# Run mutation testing ephemerally — source mounted RO, mutated copy
# lives in container overlay and is destroyed on `--rm`. Routes through
# justfile.container's `mutation-test` (80% kill threshold via
# mutmut export-cicd-stats).
mutation-test:
    just _container-ephemeral mutation-test

# DEPRECATED: legacy host-side mutation runner. Mutates files in the host
# working tree; if the run crashes the mutated source is left behind.
# Retained only as an escape hatch for environments where docker is
# unavailable. Prefer `just mutation-test`.
mutation-test-host-legacy:
    #!/usr/bin/env bash
    set -euo pipefail
    echo "WARNING: deprecated, runs on host. Use just mutation-test instead." >&2
    # Capture mutmut run output to parse the final progress line
    uv run --extra dev mutmut run 2>&1 | tee /tmp/mutmut_output.txt || true
    # Final line format: ⠧ 709/709  🎉 390 🫥 226  ⏰ 2  🤔 0  🙁 91  🔇 0  🧙 0
    final_line=$(grep '🎉' /tmp/mutmut_output.txt | tail -1)
    killed=$(echo "$final_line" | sed -n 's/.*🎉 *\([0-9]*\).*/\1/p')
    survived=$(echo "$final_line" | sed -n 's/.*🙁 *\([0-9]*\).*/\1/p')
    killed=${killed:-0}
    survived=${survived:-0}
    total=$((killed + survived))
    if [ "$total" -eq 0 ]; then
        echo "ERROR: No mutants were tested"
        exit 1
    fi
    rate=$((killed * 100 / total))
    echo "Mutation kill rate: ${rate}% (${killed}/${total}, ${survived} survived)"
    if [ "$rate" -lt 80 ]; then
        echo "FAIL: Kill rate ${rate}% is below 80% threshold"
        exit 1
    fi
    echo "PASS: Kill rate meets 80% threshold"

# Show mutation testing results
mutation-test-results:
    uv run --extra dev mutmut results

# Generate mutation testing HTML report
mutation-test-html:
    uv run --extra dev mutmut html

# Build package
build: generate-proto
    uv build

# Publish to TestPyPI
publish-test: build
    uv run --with twine twine upload --repository testpypi dist/*

# Publish to PyPI
publish: build
    uv run --with twine twine upload dist/*

# Clean build artifacts
clean:
    rm -rf dist/ build/ *.egg-info/ htmlcov/ .mutmut-cache .pytest_cache __pycache__

# Check formatting (alias `check` for cross-language parity).
fmt: generate-proto
    uv run ruff check . --exclude angzarr-project
    uv run black --check .

# Cross-language alias — `just check` runs lint+fmt-check in every lang.
check: fmt lint

# Auto-format code
fmt-fix: generate-proto
    uv run ruff check --fix . --exclude angzarr-project
    uv run black .

# =============================================================================
# Submodule management
# =============================================================================
# Submodules are protected by two layers:
#   1. Filesystem: chmod a-w on the submodule tree (recipes below).
#   2. Git hooks: install-submodule-hooks plants a pre-commit hook inside
#      each submodule's .git/modules/<name>/hooks/, and the parent's
#      pre-commit (via lefthook) runs check-submodules-clean. Both block
#      edits with a message pointing at the canonical repo.
# Use `bump-angzarr-project` to update — it unlocks, pulls the tracking
# branch, stages the new pointer, then relocks.

# Lock submodules read-only (filesystem enforcement).
submodules-lock:
    chmod -R a-w angzarr-project

# Unlock submodules for manual edits. Remember to `submodules-lock` after.
submodules-unlock:
    chmod -R u+w angzarr-project

# install-submodule-hooks and check-submodules-clean are provided by the
# shared submodule.just imported at the top of this file from the
# angzarr-project submodule. Edit them there.

# Bump angzarr-project to latest on its tracking branch.
bump-angzarr-project:
    chmod -R u+w angzarr-project
    git submodule update --remote --merge angzarr-project
    git add angzarr-project
    chmod -R a-w angzarr-project
