#!/usr/bin/env python3
"""Prove a Python project the angzarr CLI scaffolded (``angzarr scaffold
python``) works against this repository's package.

- every dispatch module the CLI generated (``_gen/**/*_angzarr.py``)
  imports: its message types, the framework types and the router resolve;
- the project's ``main.host()`` builds its ComponentHost, registering the
  domain on the router;
- its host answers Describe with ``_descriptor.DESCRIPTOR``, the
  descriptor the CLI generated, unchanged (decision 14.42);
- that descriptor is hand-replaceable: ``describe`` builds the same from the
  dispatch ``main.register`` registers, with the descriptor's rules version
  (a process manager's starting types given as generated).

    check_scaffold.py <project dir> <domain>

The project's ``src`` goes on sys.path; the package is ``angzarr_<domain>``.
"""

from __future__ import annotations

import importlib
import pathlib
import sys

from angzarr_client import describe


class _Recorder:
    """Stands in for a ComponentHost: records what ``main.register`` adds."""

    def __init__(self) -> None:
        self.added: list[tuple] = []

    def __getattr__(self, name: str):
        if not name.startswith("add_"):
            raise AttributeError(name)

        def add(dispatch, descriptor):
            self.added.append((dispatch, descriptor))
            return self

        return add


def check(project: pathlib.Path, domain: str) -> list[str]:
    src = project / "src"
    package = f"angzarr_{domain.replace('-', '_')}"
    problems: list[str] = []
    wiring = sorted((src / package / "_gen").rglob("*_angzarr.py"))
    if not wiring:
        return [f"no _gen/**/*_angzarr.py under {src / package}"]
    for path in wiring:
        name = ".".join(path.relative_to(src).with_suffix("").parts)
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 — reported, not raised
            problems.append(f"{name}: import failed: {exc!r}")
    try:
        main = importlib.import_module(f"{package}.main")
        host = main.host()
    except Exception as exc:  # noqa: BLE001 — reported, not raised
        return [*problems, f"{package}.main: host() failed: {exc!r}"]
    try:
        descriptor = importlib.import_module(f"{package}._descriptor").DESCRIPTOR
        if not descriptor.name:
            problems.append(f"{package}._descriptor: the descriptor names no component")
        if host.descriptor is not descriptor:
            problems.append(f"{package}.main: the host describes {host.descriptor!r}")
        recorder = _Recorder()
        main.register(recorder)
        for dispatch, generated in recorder.added:
            by_hand = describe(
                dispatch,
                rules_version=generated.rules_version,
                starting_types=generated.starting_types,
            )
            if by_hand != generated:
                problems.append(
                    f"{package}: describe() builds {by_hand!r}, not the generated "
                    f"{generated!r}"
                )
        if not problems:
            print(
                f"ok {package}: {descriptor.name} registered, {len(wiring)} dispatch modules,"
                f" {len(recorder.added)} descriptors as describe() builds them"
            )
    finally:
        host.stop()
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    project = pathlib.Path(argv[1]).resolve()
    sys.path.insert(0, str(project / "src"))
    problems = check(project, argv[2])
    for p in problems:
        print(f"FAIL {p}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
