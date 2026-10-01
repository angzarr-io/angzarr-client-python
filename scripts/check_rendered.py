#!/usr/bin/env python3
"""Prove a tree rendered by this repository's codegen templates works.

For every component wiring module (``*_angzarr.py``) under the tree:

- the module imports (its protobuf types, the framework types and the router
  binding all resolve);
- the scaffold stub beside it (``*_angzarr_handler.py``) declares every
  method of the ``<Component>Handler`` protocol with the protocol's
  parameters;
- ``register_<component>`` registers the stub on a fresh router — the
  router-ffi library accepts the component's descriptor.

    check_rendered.py <dir containing the rendered package>/<package>

The rendered tree's parent directory goes on sys.path; the tree is imported
as the package named by its last path component.
"""

from __future__ import annotations

import importlib
import inspect
import pathlib
import sys
import typing


def _module_name(root: pathlib.Path, path: pathlib.Path) -> str:
    rel = path.relative_to(root.parent).with_suffix("")
    return ".".join(rel.parts)


def check(root: pathlib.Path) -> list[str]:
    from angzarr_client.router import Router

    problems: list[str] = []
    wiring = sorted(root.rglob("*_angzarr.py"))
    if not wiring:
        return [f"no *_angzarr.py under {root}"]
    for path in wiring:
        name = _module_name(root, path)
        mod = importlib.import_module(name)
        protocols = [
            obj
            for obj in vars(mod).values()
            if inspect.isclass(obj)
            and obj.__module__ == name
            and typing.Protocol in obj.__mro__
        ]
        if len(protocols) != 1:
            problems.append(f"{name}: {len(protocols)} handler protocols, want 1")
            continue
        protocol = protocols[0]
        stub_path = path.with_name(path.stem + "_handler.py")
        stub_mod = importlib.import_module(_module_name(root, stub_path))
        stubs = [
            obj
            for obj in vars(stub_mod).values()
            if inspect.isclass(obj) and obj.__module__ == stub_mod.__name__
        ]
        if len(stubs) != 1:
            problems.append(f"{stub_mod.__name__}: {len(stubs)} stub classes, want 1")
            continue
        stub = stubs[0]
        methods = [
            m
            for m in vars(protocol)
            if not m.startswith("_") and callable(getattr(protocol, m))
        ]
        for method in methods:
            impl = getattr(stub, method, None)
            if impl is None:
                problems.append(f"{stub.__name__} lacks {protocol.__name__}.{method}")
                continue
            want = list(inspect.signature(getattr(protocol, method)).parameters)
            got = list(inspect.signature(impl).parameters)
            if want != got:
                problems.append(
                    f"{stub.__name__}.{method}{got} != {protocol.__name__}.{method}{want}"
                )
        registers = [f for n, f in vars(mod).items() if n.startswith("register_")]
        if len(registers) != 1:
            problems.append(f"{name}: {len(registers)} register_ functions, want 1")
            continue
        router = Router()
        try:
            registers[0](router, stub())
        except Exception as exc:  # noqa: BLE001 — reported, not raised
            problems.append(f"{name}: registration failed: {exc!r}")
        finally:
            router.close()
        print(
            f"ok {name}: {protocol.__name__} ({len(methods)} methods) registered via {stub.__name__}"
        )
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    root = pathlib.Path(argv[1]).resolve()
    sys.path.insert(0, str(root.parent))
    problems = check(root)
    for p in problems:
        print(f"FAIL {p}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
