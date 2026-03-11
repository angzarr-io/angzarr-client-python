# angzarr-client-python commands

set shell := ["bash", "-c"]

default:
    @just --list

# Generate proto code from buf registry
proto:
    buf generate
    python scripts/generate_protos.py

# Sync feature files from angzarr core
sync-features:
    bash scripts/sync-features.sh

# Run tests
test: sync-features
    uv run --extra dev pytest tests/ -v

# Run tests with coverage
coverage: sync-features
    uv run --extra dev pytest tests/ --cov=angzarr_client --cov-report=term-missing --cov-report=html

# Run mutation testing
mutate:
    uv run --extra dev mutmut run

# Show mutation testing results
mutate-results:
    uv run --extra dev mutmut results

# Generate mutation testing HTML report
mutate-html:
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
