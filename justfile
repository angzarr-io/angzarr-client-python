# angzarr-client-python commands

set shell := ["bash", "-c"]

default:
    @just --list

# Generate proto code from submodule
proto:
    buf generate
    uv run python scripts/generate_protos.py

# Sync feature files from angzarr core
sync-features:
    bash scripts/sync-features.sh

# Run tests
test: sync-features
    uv run --extra dev pytest tests/ -v

# Run tests with coverage
coverage: sync-features
    uv run --extra dev pytest tests/ --cov=angzarr_client --cov-report=term-missing --cov-report=html

# Run mutation testing (70% kill rate threshold)
mutation-test:
    #!/usr/bin/env bash
    set -euo pipefail
    uv run --extra dev mutmut run || true
    # mutmut v3.5 final line: ⠧ 709/709  🎉 390 🫥 226  ⏰ 2  🤔 0  🙁 91  🔇 0  🧙 0
    result=$(uv run --extra dev mutmut results 2>&1 || true)
    killed=$(echo "$result" | grep -oP '🎉\s*\K[0-9]+' | tail -1 || echo "")
    survived=$(echo "$result" | grep -oP '🙁\s*\K[0-9]+' | tail -1 || echo "")
    if [ -z "$killed" ] || [ "$killed" = "0" ]; then
        killed=$(echo "$result" | grep -oP 'Killed: \K[0-9]+' || echo "0")
        survived=$(echo "$result" | grep -oP 'Survived: \K[0-9]+' || echo "0")
    fi
    total=$((killed + survived))
    if [ "$total" -eq 0 ]; then
        echo "ERROR: No mutants were tested"
        exit 1
    fi
    rate=$((killed * 100 / total))
    echo "Mutation kill rate: ${rate}% (${killed}/${total}, ${survived} survived)"
    if [ "$rate" -lt 70 ]; then
        echo "FAIL: Kill rate ${rate}% is below 70% threshold"
        exit 1
    fi
    echo "PASS: Kill rate meets 70% threshold"

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
