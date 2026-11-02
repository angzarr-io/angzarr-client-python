"""A code and its args: how an error shows itself, and its English.

Every error angzarr answers is a code and an argument map, never text
(decision 14.37). This module is the one place both are turned into
something a person reads:

- :func:`display`, the error's own text: its code then `` name=value`` per
  arg, a value quoted where it would not parse back (:func:`quote`). Never
  English: it is what ``str()`` of an error is, for logs.
- :func:`render`, its English, looked up by the code in a Fluent catalog
  and filled in with the args: the spec's catalogs of the framework's
  reason keys (each package's ``reasons.en-US.ftl``, shipped beside its
  generated protos: core's, the local-process transport's and the
  distributed transport's; decisions 14.60 and 14.64) for a framework key, this package's own
  (``locales/en-US/angzarr-client.ftl``) for one of its own codes. A code
  in neither renders as :func:`display` does.

The catalogs use a subset of Fluent: messages over one or more lines, ``{ $arg }``
placeables, and select expressions on an arg (``{ $n -> [one] ... *[other] ... }``,
``one`` matching the number 1, as en-US plurals do). No Fluent library is
needed for that subset.
"""

from __future__ import annotations

import functools
import pathlib
import re
from collections.abc import Mapping

_HERE = pathlib.Path(__file__).resolve().parent
# The spec's catalog of each package with reason keys (decision 14.64):
# core's, the local-process transport's (a child's keys) and the
# distributed transport's (a coordinator's).
SPEC_CATALOGS = tuple(
    _HERE / "proto" / package / "reasons.en-US.ftl"
    for package in (
        "io/angzarr/v1",
        "io/angzarr/transport/embedded/v1",
        "io/angzarr/transport/distributed/v1",
    )
)
CLIENT_CATALOG = _HERE / "locales" / "en-US" / "angzarr-client.ftl"

_MESSAGE = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*) =(?: (.*))?$")
_VARIANT = re.compile(r"(\*?)\[([A-Za-z0-9_-]+)\]")


def quote(value: str) -> str:
    """A display value: bare, unless empty or holding whitespace, ``=``,
    ``"`` or ``\\`` (decision 14.37), then double-quoted with ``"`` and
    ``\\`` escaped, so every line parses back."""
    if value and not any(c.isspace() or c in '="\\' for c in value):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def display(code: str, args: Mapping[str, object] | None = None) -> str:
    """``code`` then `` name=value`` for each arg, in order: an error's own
    text, never English."""
    return code + "".join(
        f" {name}={quote(str(value))}" for name, value in (args or {}).items()
    )


def parse(text: str) -> dict[str, str]:
    """A catalog's messages, each id with its pattern (continuation lines
    joined by newlines). Comments and blank lines end a message."""
    messages: dict[str, str] = {}
    current: str | None = None
    for line in text.splitlines():
        if line.startswith(" ") and current is not None:
            messages[current] += "\n" + line.strip()
            continue
        match = _MESSAGE.match(line)
        current = match.group(1) if match else None
        if current is not None:
            messages[current] = match.group(2) or ""
    return messages


@functools.cache
def _catalog(path: pathlib.Path) -> dict[str, str]:
    return parse(path.read_text(encoding="utf-8"))


def catalog() -> dict[str, str]:
    """Every message this package renders from: the spec's keys, every
    package's, then its own codes."""
    spec = {key: text for path in SPEC_CATALOGS for key, text in _catalog(path).items()}
    return {**spec, **_catalog(CLIENT_CATALOG)}


def _close(text: str, start: int) -> int:
    """The index of the ``}`` closing the placeable opened at ``start``."""
    depth = 0
    for index in range(start, len(text)):
        depth += {"{": 1, "}": -1}.get(text[index], 0)
        if depth == 0:
            return index
    raise ValueError(f"an unclosed placeable: {text[start:]!r}")


def _matches(key: str, value: object) -> bool:
    return key == str(value) or (key == "one" and str(value) == "1")


def _select(selector: str, body: str, args: Mapping[str, object]) -> str:
    """The variant of ``body`` the arg ``selector`` picks, rendered: the one
    whose key matches its value, else the default (``*``)."""
    value = args.get(selector.strip().removeprefix("$"))
    marks = list(_VARIANT.finditer(body))
    chosen = None
    default = ""
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(body)
        variant = body[mark.end() : end].strip()
        if mark.group(1):
            default = variant
        if chosen is None and value is not None and _matches(mark.group(2), value):
            chosen = variant
    return _pattern(chosen if chosen is not None else default, args)


def _placeable(inner: str, args: Mapping[str, object]) -> str:
    """A placeable's text: a select expression's variant, an arg's value
    (shown as ``{$name}`` when the args lack it), or, for anything else the
    subset does not read, the placeable as written."""
    selector, arrow, body = inner.partition("->")
    if arrow:
        return _select(selector, body, args)
    reference = inner.strip()
    if not reference.startswith("$"):
        return "{" + inner + "}"
    name = reference.removeprefix("$")
    return str(args[name]) if name in args else "{$" + name + "}"


def _pattern(text: str, args: Mapping[str, object]) -> str:
    out: list[str] = []
    index = 0
    while index < len(text):
        if text[index] != "{":
            out.append(text[index])
            index += 1
            continue
        end = _close(text, index)
        out.append(_placeable(text[index + 1 : end], args))
        index = end + 1
    return "".join(out)


def render(code: str, args: Mapping[str, object] | None = None) -> str:
    """``code``'s English with ``args`` filled in, from the catalogs; a code
    neither catalog holds as its :func:`display`."""
    pattern = catalog().get(code)
    if pattern is None:
        return display(code, args)
    return _pattern(pattern, args or {})
