# angzarr-client-python commands

set shell := ["bash", "-c"]

default:
    @just --list

# Generate proto code from submodule
proto:
    buf generate
    uv run python scripts/generate_protos.py

# Framework-harness cucumber (pytest-bdd; feature files from angzarr-project/features/client/)
test-client-unit:
    uv run --extra dev pytest tests/client/ -v

# Plain pytest, excluding the BDD subset
test-pytest:
    uv run --extra dev pytest tests/ -v --ignore=tests/client

# Full suite
test: test-pytest test-client-unit

# Full suite with verbose output (matches Rust's `just test-verbose`)
test-verbose:
    uv run --extra dev pytest tests/ -v -s

# Lint only (ruff). `fmt` already runs black --check + ruff check; this is
# a cross-language alias matching Rust's `just lint` = `cargo clippy -D warnings`.
lint:
    uv run ruff check .

# Run tests with coverage
coverage:
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

# Run mutation testing (80% kill rate threshold)
mutation-test:
    #!/usr/bin/env bash
    set -euo pipefail
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
build: proto
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

# Check formatting
fmt:
    uv run ruff check . --exclude angzarr-project
    uv run black --check .

# Auto-format code
fmt-fix:
    uv run ruff check --fix . --exclude angzarr-project
    uv run black .

# =============================================================================
# Submodule management
# =============================================================================
# The angzarr-project submodule is kept chmod a-w so accidental edits (Claude,
# editors, scripts) fail loudly. Use `bump-angzarr-project` to update — it
# unlocks, pulls the tracking branch, stages the new pointer, then relocks.

# Lock submodules read-only (filesystem enforcement).
submodules-lock:
    chmod -R a-w angzarr-project

# Unlock submodules for manual edits. Remember to `submodules-lock` after.
submodules-unlock:
    chmod -R u+w angzarr-project

# Bump angzarr-project to latest on its tracking branch.
bump-angzarr-project:
    chmod -R u+w angzarr-project
    git submodule update --remote --merge angzarr-project
    git add angzarr-project
    chmod -R a-w angzarr-project
