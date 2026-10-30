# angzarr-client-python commands. CI calls these recipes and nothing else.

set shell := ["bash", "-euo", "pipefail", "-c"]

TOP := justfile_directory()

# Shared hooks from the spec submodule (scan-secrets, submodule guards).
import? 'angzarr-project/submodule.just'

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

# The test gate the git hooks run (pre-commit, pre-merge-commit), under the
# red-on-branches rule: red tests (pytest exit 1, failing tests) are committed
# on any branch but the default one, with a warning that the branch cannot
# merge until they pass, and refused on the default branch and on a merge
# commit. Everything else (preparing the environment, collection errors, an
# interrupted run) fails everywhere.
test-commit merging="":
    #!/usr/bin/env bash
    set -uo pipefail
    cd {{TOP}}
    just prepare || exit 1
    uv run --extra dev pytest tests/ -q
    status=$?
    [ "$status" -eq 0 ] && exit 0
    [ "$status" -ne 1 ] && exit "$status"
    default="$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null)"
    default="${default#origin/}"
    default="${default:-main}"
    branch="$(git symbolic-ref --quiet --short HEAD 2>/dev/null || echo HEAD)"
    if [ -n "{{merging}}" ]; then
        echo "FAIL tests: red tests cannot be merged" >&2
        exit 1
    fi
    if [ "$branch" = "$default" ] || [ "$branch" = "HEAD" ]; then
        echo "FAIL tests: red tests cannot be committed on $branch; commit them on a branch" >&2
        exit 1
    fi
    echo "RED  tests: red tests committed on $branch, allowed off $default; $branch cannot merge into $default until they pass" >&2
    exit 0

# Full suite with verbose output
test-verbose: prepare
    cd {{TOP}} && uv run --extra dev pytest tests/ -v

# Lint (ruff)
lint:
    cd {{TOP}} && uv run --extra dev ruff check .

# Architecture lint: the import contracts in .importlinter (package layering)
# and .importlinter-external (third-party imports), via import-linter. The
# graph is rebuilt every run: the mtime-keyed cache goes stale across checkouts.
archlint: proto
    cd {{TOP}} && uv run --extra dev lint-imports --no-cache --config .importlinter
    cd {{TOP}} && uv run --extra dev lint-imports --no-cache --config .importlinter-external

# Run tests with coverage
coverage: prepare
    cd {{TOP}} && uv run --extra dev pytest tests/ --cov=angzarr_client --cov-report=term-missing

# Build Sphinx HTML docs into docs/_build/html.
docs:
    cd {{TOP}} && uv run --extra docs sphinx-build -b html --keep-going docs docs/_build/html

# Mutation testing, the full pass (the weekly workflow's): every mutant of
# the [tool.mutmut] sources, judged by MUTATION_THRESHOLD over the evaluable
# mutants.
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
    echo "Kill rate: ${rate}% (${killed}/${evaluated} evaluable; ${no_tests} untested), threshold {{MUTATION_THRESHOLD}}%"
    if [ "$rate" -lt {{MUTATION_THRESHOLD}} ]; then echo "FAIL: kill rate below {{MUTATION_THRESHOLD}}%"; exit 1; fi

# Mutation kill-rate threshold, percent: the share of evaluable mutants (all
# but those no test reaches) that tests kill, for `mutation-test` and
# `mutation-push`.
MUTATION_THRESHOLD := "90"

# lefthook's pre-push step, given the remote's name and git's pushed refs on
# stdin: mutmut over what each pushed ref changes, judged by
# MUTATION_THRESHOLD. mutmut has no diff option, so the diff is narrowed to
# functions: each changed line of a [tool.mutmut] source file is mapped (by
# `ast`) to the top-level function or class method holding it, and mutmut
# runs only those functions' mutants (`mutmut run <module>.x_<function>__mutmut_*`,
# `<module>.xǁ<Class>ǁ<method>__mutmut_*`, mutmut 3's names). A changed line in
# no function (module or class body) has no mutants. The diff's base is the
# merge base of the commit pushed and the remote's current tip of that ref (a
# fast-forward's base is that tip); for a new ref, or a tip not fetched here,
# the merge base with the remote's default branch (<remote>/HEAD, else main).
# It runs in the pushed commit's tree checked out alone (build/push-tree, its
# submodules, recursively, exported from this checkout's, its .tools copied
# from here), so an unpushed edit cannot pass or fail it. A deleted ref, and a ref changing
# no function of a source file, pass at once. The log is
# build/logs/mutation-push.log.
#
# Red tests (mutmut's clean run or stats collection fails) cannot be judged:
# pushed to a branch other than the default one they pass, with a warning that
# the branch cannot merge until they pass; pushed to the default branch they
# are refused. `lefthook` is what lefthook.yml passes to stop lefthook
# skipping it, and is ignored.
[positional-arguments]
mutation-push remote *lefthook:
    #!/usr/bin/env bash
    set -euo pipefail
    top='{{TOP}}'
    remote=$1
    default=$(git -C "$top" symbolic-ref --quiet --short "refs/remotes/$remote/HEAD" 2>/dev/null) && default=${default#"$remote"/} || default=main
    tree="$top/build/push-tree"
    mkdir -p "$top/build/logs"
    log="$top/build/logs/mutation-push.log"
    exec 9> "$top/build/.push-tree.lock"
    flock 9
    # git exports repository-locating variables (GIT_DIR, ...) into a hook;
    # with them, a git -C the push tree or a submodule would act on this
    # checkout instead.
    unset $(git rev-parse --local-env-vars)
    pushed=0
    while read -r local_ref local_sha remote_ref remote_sha; do
        [ -n "${local_sha:-}" ] || continue
        [ "${local_sha//0/}" = "" ] && continue  # a deletion
        pushed=1
        target=${remote_ref#refs/heads/}
        base=
        if [ -n "${remote_sha//0/}" ] && git -C "$top" cat-file -e "$remote_sha^{commit}" 2>/dev/null; then
            base=$(git -C "$top" merge-base "$remote_sha" "$local_sha") || base=
        fi
        if [ -z "$base" ]; then
            for ref in "refs/remotes/$remote/$default" "refs/heads/$default"; do
                if git -C "$top" rev-parse --verify --quiet "$ref^{commit}" > /dev/null; then
                    base=$(git -C "$top" merge-base "$ref" "$local_sha") && break
                fi
            done
        fi
        if [ -z "$base" ]; then
            echo "FAIL mutation-push: $local_ref shares no history with $remote's tip or with $default" >&2
            exit 1
        fi
        range="$(git -C "$top" rev-parse --short "$base")..$(git -C "$top" rev-parse --short "$local_sha") to $remote/$target"
        mapfile -t globs < <(just --justfile "$top/justfile" _mutation-globs "$base" "$local_sha" | grep .)
        if [ "${#globs[@]}" -eq 0 ]; then
            echo "ok   mutation-push ($range: no mutants, the push changes no function of a mutated source)"
            continue
        fi
        # The pushed commit's tree, alone, no hook run for it.
        if ! git -C "$tree" rev-parse --git-dir > /dev/null 2>&1; then
            rm -rf "$tree"
            git -C "$top" worktree prune
            git -c core.hooksPath=/dev/null -C "$top" worktree add --quiet --detach --no-checkout "$tree" "$local_sha"
        fi
        git -c core.hooksPath=/dev/null -C "$tree" checkout --quiet --detach --force "$local_sha"
        git -C "$tree" clean -fdq
        # Each submodule (recursively) at the commit its parent records,
        # exported from this checkout's own submodule repositories.
        export_submodules() {
            local src=$1 commit=$2 dest=$3 _ path sub
            git -C "$src" cat-file -e "$commit:.gitmodules" 2>/dev/null || return 0
            while read -r _ path; do
                sub=$(git -C "$src" ls-tree "$commit" "$path" | awk '{print $3}')
                [ -n "$sub" ] || continue
                if ! git -C "$src/$path" cat-file -e "$sub^{commit}" 2>/dev/null; then
                    git -C "$src/$path" fetch --quiet origin "$sub" || {
                        echo "FAIL mutation-push: $src/$path $sub, a pushed submodule commit, is not here (git submodule update --init --recursive)" >&2
                        return 1
                    }
                fi
                rm -rf "$dest/$path"
                mkdir -p "$dest/$path"
                git -C "$src/$path" archive "$sub" | tar -x -C "$dest/$path"
                export_submodules "$src/$path" "$sub" "$dest/$path" || return 1
            done < <(git -C "$src" show "$commit:.gitmodules" | git config -f - --get-regexp '^submodule\..*\.path$')
        }
        export_submodules "$top" "$local_sha" "$tree" || exit 1
        if [ ! -d "$tree/.tools" ] && [ -d "$top/.tools" ]; then cp -a "$top/.tools" "$tree/.tools"; fi
        echo "mutation-push ($range): ${#globs[@]} functions: ${globs[*]}" > "$log"
        rc=0
        (cd "$tree" && just --justfile "$tree/justfile" mutation-functions "${globs[@]}") >> "$log" 2>&1 9>&- || rc=$?
        case "$rc" in
            0) echo "ok   mutation-push ($range): $(tail -1 "$log")" ;;
            4)
                if [ "$remote_ref" != "refs/heads/$default" ]; then
                    echo "RED  mutation-push: $range not judged, the tests are red; allowed off $default, which $target cannot merge into until they pass"
                else
                    echo "FAIL mutation-push: $range: red tests cannot be pushed to $remote/$target (full log: build/logs/mutation-push.log)" >&2
                    exit 1
                fi ;;
            *)
                echo "FAIL mutation-push: $range (full log: build/logs/mutation-push.log)" >&2
                sed -n '/^Kill rate\|^FAIL\|^survived:\|^  /p' "$log" >&2
                exit 1 ;;
        esac
    done
    [ "$pushed" = 1 ] || echo "ok   mutation-push (no commit pushed: nothing to mutate)"

# The mutmut name globs of the functions the diff BASE..COMMIT changes in the
# [tool.mutmut] source files, one per line (see `mutation-push`). A deleted
# line counts against the function around where it was.
[positional-arguments]
_mutation-globs base commit:
    #!/usr/bin/env python3
    import ast, re, subprocess, sys, tomllib
    base, commit = sys.argv[1], sys.argv[2]
    top = "{{TOP}}"
    def git(*args):
        return subprocess.run(["git", "-C", top, *args], check=True, capture_output=True, text=True).stdout
    config = tomllib.loads(git("show", f"{commit}:pyproject.toml"))
    sources = config["tool"]["mutmut"]["source_paths"]
    diff = git("diff", "-U0", "--no-color", "--no-ext-diff", "--no-renames", base, commit, "--", *sources)
    lines_by_path, path = {}, None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = None if line == "+++ /dev/null" else line[6:]
        elif line.startswith("@@") and path and path.endswith(".py"):
            start, _, count = re.match(r"@@ -\S+ \+(\d+)(,(\d+))? @@", line).groups()
            start, count = int(start), int(count if count is not None else 1)
            # A pure deletion (count 0) sits after line `start`: its neighbours.
            span = range(start, start + count) if count else (start, start + 1)
            lines_by_path.setdefault(path, set()).update(span)
    globs = []
    for path, changed in sorted(lines_by_path.items()):
        module = path[: -len(".py")].replace("/", ".")
        module = module.removeprefix("src.")
        module = module.removesuffix(".__init__")
        tree = ast.parse(git("show", f"{commit}:{path}"))
        def spans(node):
            first = min([node.lineno] + [d.lineno for d in node.decorator_list])
            return range(first, node.end_lineno + 1)
        for node in tree.body:
            funcs = []
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                funcs.append((f"x_{node.name}", node))
            elif isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        funcs.append((f"xǁ{node.name}ǁ{item.name}", item))
            for mangled, fn in funcs:
                if changed.intersection(spans(fn)):
                    globs.append(f"{module}.{mangled}__mutmut_*")
    print("\n".join(globs))

# mutmut over the named mutants only (GLOBS, mutmut 3 name globs), judged by
# MUTATION_THRESHOLD over every mutant they match, those no test reaches
# counted as survivors (the full pass leaves them out): `mutation-push`'s run, in the
# pushed commit's tree. Exits 4 when the tests fail unmutated (mutmut's clean
# run or stats collection), so no mutant can be judged; its last line is the
# verdict.
[positional-arguments]
mutation-functions +globs: prepare
    #!/usr/bin/env bash
    set -uo pipefail
    cd {{TOP}}
    # A fresh mutants/: mutmut copies the tests in but never removes one the
    # tree no longer has.
    rm -rf mutants
    out=$(uv run --extra dev mutmut run "$@" 2>&1)
    rc=$?
    printf '%s\n' "$out"
    if [ "$rc" -ne 0 ]; then
        if grep -q "Filtered for specific mutants, but nothing matches" <<< "$out"; then
            echo "no mutants: mutmut generates none in the changed functions"
            exit 0
        fi
        if grep -qE "Failed to run clean test|failed to collect stats" <<< "$out"; then
            echo "FAIL: the tests fail unmutated; no mutant can be judged"
            exit 4
        fi
        echo "FAIL: mutmut failed before a verdict (exit $rc)"
        exit 1
    fi
    uv run --extra dev python - "{{MUTATION_THRESHOLD}}" "$@" <<'PY'
    import fnmatch, json, pathlib, sys
    threshold, globs = int(sys.argv[1]), sys.argv[2:]
    status = {}
    for meta in pathlib.Path("mutants").rglob("*.py.meta"):
        for key, code in json.loads(meta.read_text())["exit_code_by_key"].items():
            if any(fnmatch.fnmatch(key, g) for g in globs):
                status[key] = code
    # Unlike the full pass, a mutant no test reaches counts against the
    # rate: a changed function no test runs is what this gate is for.
    killed = sorted(k for k, c in status.items() if c in (1, 3))
    no_tests = [k for k, c in status.items() if c in (5, 33)]
    judged = len(status)
    others = sorted(k for k, c in status.items() if c not in (1, 3))
    if judged == 0:
        print("no mutants in the changed functions")
        sys.exit(0)
    rate = len(killed) * 100 // judged
    line = f"Kill rate: {rate}% ({len(killed)}/{judged}; {len(no_tests)} no test reaches, counted survived), threshold {threshold}%"
    if len(killed) * 100 >= threshold * judged:
        print(line)
        sys.exit(0)
    print("survived:")
    for k in others:
        print(f"  {k} (exit {status[k]})")
    print("FAIL " + line)
    sys.exit(1)
    PY

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

# The CI entry point: format, lint, architecture lint, tests, and the
# rendered-code check.
ci: fmt archlint test codegen-check
