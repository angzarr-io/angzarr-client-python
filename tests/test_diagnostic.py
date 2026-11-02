"""angzarr_client.diagnostic: an error's text and its English (decisions
14.37, 14.60), and the catalogs it renders from."""

from __future__ import annotations

import re

import pytest

from angzarr_client import diagnostic
from angzarr_client.error_codes import codes
from angzarr_client.proto.io.angzarr.transport.distributed.v1 import (
    reason_args as distributed_args,
)
from angzarr_client.proto.io.angzarr.transport.distributed.v1 import (
    reasons_pb2 as distributed_reasons,
)
from angzarr_client.proto.io.angzarr.transport.embedded.v1 import (
    reason_args as embedded_args,
)
from angzarr_client.proto.io.angzarr.transport.embedded.v1 import (
    reasons_pb2 as embedded_reasons,
)
from angzarr_client.proto.io.angzarr.v1 import reason_args, reasons_pb2

_VARIABLE = re.compile(r"\$([A-Za-z][A-Za-z0-9_-]*)")

# Every package's key enums (decision 14.64): core's three, then each
# transport's.
_ENUMS = (
    reasons_pb2.RouterReason,
    reasons_pb2.HostReason,
    reasons_pb2.CoordinatorReason,
    embedded_reasons.HostReason,
    distributed_reasons.CoordinatorReason,
)

# Every key's args, every package's.
_ARGS = {**reason_args.ARGS, **embedded_args.ARGS, **distributed_args.ARGS}


def _live_keys() -> list[str]:
    return [v.name for e in _ENUMS for v in e.DESCRIPTOR.values if v.number != 0]


def _spec() -> dict[str, str]:
    return {
        key: text
        for path in diagnostic.SPEC_CATALOGS
        for key, text in diagnostic._catalog(path).items()
    }


def _variables(pattern: str) -> set[str]:
    return set(_VARIABLE.findall(pattern))


# --- display: the code then its args, never English --------------------------


@pytest.mark.parametrize(
    ("value", "shown"),
    [
        ("x", "x"),
        ("", '""'),
        ("a b", '"a b"'),
        ("a=b", '"a=b"'),
        ('a"b', '"a\\"b"'),
        ("a\\b", '"a\\\\b"'),
    ],
)
def test_a_value_is_quoted_only_where_it_would_not_parse_back(value, shown):
    assert diagnostic.quote(value) == shown
    assert diagnostic.display("K", {"v": value}) == f"K v={shown}"


def test_display_is_the_code_alone_without_args():
    assert diagnostic.display("K") == "K"
    assert diagnostic.display("K", {"a": 1, "b": "two"}) == "K a=1 b=two"


# --- parse and render: the subset of Fluent the catalogs use --------------------


def test_parse_reads_messages_over_lines_and_skips_comments():
    text = "# c\n## g\n\nA = one\nB = two { $n ->\n    [one] x\n   *[other] y\n  }\nnot a message\n  indented after it\nC =\n"
    assert diagnostic.parse(text) == {
        "A": "one",
        "B": "two { $n ->\n[one] x\n*[other] y\n}",
        "C": "",
    }


def _render(pattern: str, args: dict) -> str:
    return diagnostic._pattern(pattern, args)


def test_a_placeable_is_its_arg_or_shows_it_missing():
    assert _render("a { $x } b", {"x": 3}) == "a 3 b"
    assert _render("a { $x } b", {}) == "a {$x} b"
    assert _render("no placeables", {"x": 1}) == "no placeables"


@pytest.mark.parametrize(
    ("n", "shown"), [(1, "one run"), (2, "2 runs"), (0, "0 runs"), ("1", "one run")]
)
def test_a_select_picks_one_for_1_else_the_default(n, shown):
    pattern = "{ $n ->\n[one] one run\n*[other] { $n } runs\n}"
    assert _render(pattern, {"n": n}) == shown


def test_a_select_matches_an_exact_key_before_the_default():
    pattern = "{ $r ->\n[bad-root] bad\n*[other] { $r }\n}"
    assert _render(pattern, {"r": "bad-root"}) == "bad"
    assert _render(pattern, {"r": "else"}) == "else"
    assert _render(pattern, {}) == "{$r}"


def test_a_select_ends_each_variant_at_the_next():
    pattern = "{ $k ->\n[a] A\n[b] B\n*[other] O\n}"
    assert [_render(pattern, {"k": k}) for k in ("a", "b", "z")] == ["A", "B", "O"]


def test_the_default_is_the_starred_variant_wherever_it_is():
    pattern = "{ $k ->\n*[other] O\n[one] one\n}"
    assert _render(pattern, {}) == "O"
    assert _render(pattern, {"k": 1}) == "one"


def test_a_select_with_no_match_and_no_default_is_empty():
    assert _render("[{ $k ->\n[a] A\n}]", {"k": "z"}) == "[]"


def test_a_variant_may_hold_an_arrow():
    pattern = "{ $k ->\n[a] a->b\n*[other] c\n}"
    assert _render(pattern, {"k": "a"}) == "a->b"


def test_a_placeable_that_is_no_reference_is_shown_as_written():
    assert _render('a {"lit"} b', {}) == 'a {"lit"} b'
    assert _render("{$x}", {"x": 4}) == "4"
    assert _render("{ $Xx }", {"Xx": 5}) == "5"


def test_a_continuation_with_no_message_above_it_is_skipped():
    assert diagnostic.parse("  stray\nA = one\n") == {"A": "one"}


def test_an_unclosed_placeable_is_refused():
    with pytest.raises(ValueError, match="unclosed placeable: '{ \\$x'"):
        _render("a { $x", {"x": 1})


def test_render_uses_the_catalogs_and_falls_back_to_the_text():
    assert diagnostic.render("HOST_REASON_HANDSHAKE_MALFORMED", {"line": "l"}) == (
        'malformed handshake "l": expected angzarr-handshake v1 unix:<absolute path>'
    )
    assert diagnostic.render("COMMAND_PAYLOAD_MISSING") == "command payload not set"
    assert diagnostic.render("NOT_A_CODE", {"a": "b c"}) == 'NOT_A_CODE a="b c"'
    assert diagnostic.render(
        "COORDINATOR_REASON_POLICY_INPUT_COUNT", {"component": "p", "input_count": 2}
    ) == ('component "p": a policy has 2 inputs; it must have exactly one')


# --- the catalogs ------------------------------------------------------------------


def test_the_spec_catalogs_have_a_message_for_every_live_key_and_no_other():
    assert set(_spec()) == set(_live_keys())
    assert len(_spec()) == len(_live_keys()), "a key in two catalogs"


def test_each_packages_catalog_holds_its_own_keys():
    core, embedded, distributed = (
        set(diagnostic._catalog(path)) for path in diagnostic.SPEC_CATALOGS
    )
    assert core == set(reason_args.ARGS)
    assert embedded == set(embedded_args.ARGS)
    assert distributed == set(distributed_args.ARGS)
    assert "HOST_REASON_HANDSHAKE_MALFORMED" in embedded
    assert "COORDINATOR_REASON_PAYLOAD_UNAVAILABLE" in distributed


def test_every_spec_message_names_only_args_its_key_declares():
    for key, pattern in _spec().items():
        assert _variables(pattern) <= set(_ARGS[key]), key


def test_every_spec_message_renders_with_its_declared_args():
    for key, args in _ARGS.items():
        text = diagnostic.render(key, {a: "2" for a in args})
        assert "{$" not in text and text, key


def _own_codes() -> set[str]:
    values = {v for k, v in vars(codes).items() if k.isupper() and isinstance(v, str)}
    return {v for v in values if v not in _ARGS}


def test_the_client_catalog_holds_every_own_code_and_no_other():
    own = diagnostic._catalog(diagnostic.CLIENT_CATALOG)
    assert set(own) == _own_codes()


def test_no_own_code_is_a_framework_key():
    own = diagnostic._catalog(diagnostic.CLIENT_CATALOG)
    assert not set(own) & set(_live_keys())


def test_every_framework_key_code_is_its_enum_values_name():
    framework = {v for k, v in vars(codes).items() if k.isupper() and v in _ARGS}
    assert framework <= set(_live_keys())
    assert codes.HOST_REASON_HANDSHAKE_MALFORMED in framework


def test_the_generated_args_are_the_descriptors():
    for key, args in _ARGS.items():
        enum = next(e for e in _ENUMS if key in e.DESCRIPTOR.values_by_name)
        options = enum.DESCRIPTOR.values_by_name[key].GetOptions()
        assert tuple(options.Extensions[reasons_pb2.reason].args) == args
    assert set(_ARGS) == set(_live_keys())
    assert reason_args.EXT_ERR_TXT == "ext_err_txt"


def test_every_key_declares_a_status_or_is_a_readiness_refusal():
    # Decision 14.65: one table per package, generated from the options.
    status = {
        **reason_args.STATUSES,
        **embedded_args.STATUSES,
        **distributed_args.STATUSES,
    }
    assert set(status) == set(_ARGS)
    assert {s for s in status.values() if s is not None} <= {3, 9, 10, 12, 13, 14, 15}
