# angzarr-client-python commands. CI calls these recipes and nothing else.

set shell := ["bash", "-euo", "pipefail", "-c"]

TOP := justfile_directory()

# The angzarr CLI that renders this repository's codegen templates, built from
# angzarr-cli at this revision into .tools/ (`just cli`). ANGZARR_CLI points at
# another binary instead (e.g. a local angzarr-cli build).
CLI_REV := "734b1bbdd4e913f19f8cb87f3f1dd95c06053d99"
CLI := env_var_or_default("ANGZARR_CLI", TOP / ".tools" / "angzarr")


default:
    @just --list

# Python types for the framework contract and the router ABI
# (angzarr_client/proto/, gitignored), generated with grpcio-tools and with
# imports rerooted under the package (scripts/native_build.py; the wheel build
# runs the same code).
proto:
    cd {{TOP}} && uv run --extra dev python scripts/native_build.py proto

# Build the router-ffi cdylib from the pinned angzarr-router submodule against
# this repository's angzarr-project protos, and vendor it into the package.
router-lib:
    cd {{TOP}} && uv run --extra dev python scripts/native_build.py router-lib

# Install the angzarr CLI at CLI_REV into .tools/ (skipped when ANGZARR_CLI
# names another binary).
cli:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ -n "${ANGZARR_CLI:-}" ]; then echo "using ANGZARR_CLI=$ANGZARR_CLI"; exit 0; fi
    if [ -x "{{CLI}}" ] && [ "$(cat {{TOP}}/.tools/angzarr.rev 2>/dev/null)" = "{{CLI_REV}}" ]; then exit 0; fi
    GOBIN={{TOP}}/.tools GOFLAGS=-mod=mod go install github.com/angzarr-io/angzarr-cli@{{CLI_REV}}
    mv {{TOP}}/.tools/angzarr-cli {{CLI}}
    echo "{{CLI_REV}}" > {{TOP}}/.tools/angzarr.rev

# Render this repository's templates (codegen/) over a proto tree with the CLI:
# wiring (codegen) and stubs (scaffold) for the given paths, plus their protobuf
# types, into <out>. Framework imports resolve to angzarr_client.proto; imports
# of the rendered protos resolve under <package> (the dotted package <out> is
# imported as).
render out package +paths: cli
    #!/usr/bin/env bash
    set -euo pipefail
    out="{{out}}"
    mkdir -p "$out"
    tmpl="$(mktemp --suffix=.gen.yaml)"
    trap 'rm -f "$tmpl"' EXIT
    cat > "$tmpl" <<YAML
    version: v2
    managed:
      enabled: true
      override:
        - file_option: go_package_prefix
          value: angzarr.local/gen
    plugins:
      - remote: buf.build/protocolbuffers/python:v35.1
        out: $out
      - local: ["{{CLI}}", "codegen", "python"]
        out: $out
        opt: [paths=source_relative, templates={{TOP}}/codegen]
        strategy: all
      - local: ["{{CLI}}", "scaffold", "python"]
        out: $out
        opt: [paths=source_relative, out_dir=$out, templates={{TOP}}/codegen]
        strategy: all
    YAML
    args=()
    for p in {{paths}}; do args+=(--path "$p"); done
    cd {{TOP}} && buf generate --template "$tmpl" "${args[@]}"
    framework=(io.angzarr.v1=angzarr_client.proto.io.angzarr.v1 io.angzarr.status=angzarr_client.proto.io.angzarr.status sererr=angzarr_client.proto.sererr)
    own=()
    for p in {{paths}}; do
        mod="$(echo "${p#*/proto/}" | tr / .)"
        own+=("$mod={{package}}.$mod")
    done
    uv run python scripts/fixup_gen_imports.py "$out" "${framework[@]}" "${own[@]}"

# The conformance fixture (angzarr-router/conformance/proto) rendered into
# tests/router/gen: the components the conformance steps register.
conformance-gen:
    rm -rf {{TOP}}/tests/router/gen
    just render {{TOP}}/tests/router/gen tests.router.gen angzarr-router/conformance/proto/test

# Render the angzarr-project example protos with the templates and prove the
# result loads: every wiring module imports and every scaffold stub declares
# each method of its <Component>Handler protocol.
codegen-check:
    rm -rf {{TOP}}/.codegen-check
    just render {{TOP}}/.codegen-check/gen gen angzarr-project/proto/io/angzarr/examples
    cd {{TOP}} && uv run python scripts/check_rendered.py .codegen-check/gen

# Everything the tests need: protos, the router library, the fixture wiring.
prepare: proto router-lib conformance-gen

# Unit, binding, conformance and client-feature tests.
test: prepare
    cd {{TOP}} && uv run --extra dev pytest tests/ -q

# Full suite with verbose output
test-verbose: prepare
    cd {{TOP}} && uv run --extra dev pytest tests/ -v

# Lint (ruff)
lint:
    cd {{TOP}} && uv run --extra dev ruff check .

# Run tests with coverage
coverage: prepare
    cd {{TOP}} && uv run --extra dev pytest tests/ --cov=angzarr_client --cov-report=term-missing

# Build Sphinx HTML docs into docs/_build/html.
docs:
    cd {{TOP}} && uv run --extra docs sphinx-build -b html --keep-going docs docs/_build/html

# Mutation testing (80% kill-rate gate over the evaluable mutants).
mutation-test: prepare
    #!/usr/bin/env bash
    set -euo pipefail
    cd {{TOP}}
    rm -rf mutants
    uv run --extra dev mutmut run
    uv run --extra dev mutmut export-cicd-stats
    read -r total killed no_tests < <(python3 -c "import json; d=json.load(open('mutants/mutmut-cicd-stats.json')); print(d['total'], d['killed'], d['no_tests'])")
    evaluated=$((total - no_tests))
    if [ "$evaluated" -eq 0 ]; then echo "ERROR: no evaluable mutants"; exit 1; fi
    rate=$((killed * 100 / evaluated))
    echo "Kill rate: ${rate}% (${killed}/${evaluated} evaluable; ${no_tests} untested)"
    if [ "$rate" -lt 80 ]; then echo "FAIL: kill rate below 80%"; exit 1; fi

# Build the sdist and the manylinux wheel (the wheel build generates the protos
# and builds the router library itself), then prove the wheel installs and
# loads in a clean environment.
build:
    rm -rf {{TOP}}/dist
    cd {{TOP}} && uv build
    cd {{TOP}} && uv run --isolated --no-project --with dist/*.whl python scripts/check_wheel.py

# Publish to PyPI
publish: build
    cd {{TOP}} && uv run --with twine twine upload dist/*

# Remove build outputs and caches (generated protos, router build, rendered code).
clean:
    cd {{TOP}} && rm -rf dist/ build/ *.egg-info/ htmlcov/ mutants/ .pytest_cache \
        .router-target .codegen-check tests/router/gen angzarr_client/router/_lib \
        angzarr_client/proto/io angzarr_client/proto/sererr

# Check formatting (ruff + black)
fmt:
    cd {{TOP}} && uv run --extra dev ruff check .
    cd {{TOP}} && uv run --extra dev black --check .

# Auto-format code
fmt-fix:
    cd {{TOP}} && uv run --extra dev ruff check --fix .
    cd {{TOP}} && uv run --extra dev black .

# The CI entry point: format, lint, tests, and the rendered-code check.
ci: fmt test codegen-check
