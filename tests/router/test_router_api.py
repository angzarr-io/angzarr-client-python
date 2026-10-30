"""The router's registration and error API beyond what the handler-contract
tier exercises: the shapes business code is called with, the coded errors
it fails with and how they travel, the page context accessors, the
description a component gives, and the failures the fold names."""

from __future__ import annotations

import logging

import pytest
from google.protobuf import any_pb2, wrappers_pb2
from google.rpc import error_details_pb2

from angzarr_client.proto.io.angzarr.v1 import policy_pb2, types_pb2
from angzarr_client.router import (
    WILDCARD_DOMAIN,
    AggregateDispatch,
    Bug,
    CodedError,
    Conflict,
    FactRecord,
    GrpcCode,
    Immediate,
    PageContext,
    PolicyDispatch,
    ProcessManagerDispatch,
    ProjectorDispatch,
    Rebuilder,
    Refused,
    Rejection,
    RetryableBug,
    Router,
    UnclassifiedError,
    any_decode_error,
    current_cover,
    current_page,
    pack,
)
from angzarr_client.router.context import book_context

from .contract import cover_of
from .gen import counter_pb2, order_pb2

_INCREASED = "test.counter.Increased"
_INCREASE_BY = "test.counter.IncreaseBy"


def _info(err: CodedError) -> error_details_pb2.ErrorInfo:
    info = error_details_pb2.ErrorInfo()
    assert err.status().details[0].Unpack(info)
    return info


def _raises(call, *args) -> CodedError:
    with pytest.raises(CodedError) as exc:
        call(*args)
    return exc.value


# --- coded errors ------------------------------------------------------------


def test_a_bare_coded_error_is_an_uncoded_internal_failure():
    err = CodedError()
    assert (err.code, err.grpc, err.extras, err.refusal, str(err)) == (
        "",
        GrpcCode.INTERNAL,
        {},
        None,
        "",
    )


def test_a_coded_error_keeps_a_copy_of_its_args():
    args = {"domain": "ledger"}
    err = CodedError("NOT_THERE", GrpcCode.NOT_FOUND, args)
    args["domain"] = "changed"
    assert err.extras == {"domain": "ledger"}
    assert err.grpc == 5


@pytest.mark.parametrize(
    ("value", "shown"),
    [
        ("3", "3"),
        ("", '""'),
        ("two words", '"two words"'),
        ("a=b", '"a=b"'),
        ('say "hi"', '"say \\"hi\\""'),
        ("back\\slash", '"back\\\\slash"'),
        ("tab\there", '"tab\there"'),
        ("Xy", "Xy"),
    ],
)
def test_its_text_is_its_code_then_its_args_quoted_to_parse_back(value, shown):
    err = CodedError("SEAT_TAKEN", extras={"seat": value, "table": "t1"})
    assert str(err) == f"SEAT_TAKEN seat={shown} table=t1"


def test_it_travels_as_a_status_whose_message_is_its_key():
    err = CodedError("SEAT_TAKEN", GrpcCode.ALREADY_EXISTS, {"seat": "3"})
    status = err.status()
    assert (status.code, status.message, len(status.details)) == (6, "SEAT_TAKEN", 1)
    info = _info(err)
    assert (info.reason, info.domain, dict(info.metadata)) == (
        "SEAT_TAKEN",
        "angzarr.io",
        {"seat": "3"},
    )


@pytest.mark.parametrize(
    "status",
    [
        GrpcCode.FAILED_PRECONDITION,
        GrpcCode.INVALID_ARGUMENT,
        GrpcCode.NOT_FOUND,
        GrpcCode.ALREADY_EXISTS,
        GrpcCode.OUT_OF_RANGE,
        GrpcCode.PERMISSION_DENIED,
    ],
)
def test_a_rejection_keeps_any_business_status(status):
    err = Rejection("SEAT_TAKEN", status, seat=3)
    assert (err.code, err.grpc, err.extras) == ("SEAT_TAKEN", status, {"seat": "3"})


def test_a_rejection_is_a_failed_precondition_unless_given():
    assert Rejection("VALUE_NOT_POSITIVE").grpc == GrpcCode.FAILED_PRECONDITION
    assert Rejection("X", status="FREE").extras == {"status": "FREE"}


@pytest.mark.parametrize(
    "status",
    [GrpcCode.ABORTED, GrpcCode.INTERNAL, GrpcCode.UNAVAILABLE, GrpcCode.UNKNOWN],
)
def test_a_rejection_outside_the_business_set_is_refused(status):
    names = (
        "ALREADY_EXISTS, FAILED_PRECONDITION, INVALID_ARGUMENT, NOT_FOUND, "
        "OUT_OF_RANGE, PERMISSION_DENIED"
    )
    with pytest.raises(ValueError) as exc:
        Rejection("X", status)
    assert str(exc.value) == f"a rejection's status is one of ({names}), not {status!r}"


def test_an_immediate_return_is_aborted_under_its_own_key():
    err = Immediate("PRICE_MOVED", item="apple")
    assert (err.code, err.grpc, err.extras) == (
        "PRICE_MOVED",
        GrpcCode.ABORTED,
        {"item": "apple"},
    )


def test_a_bug_is_unhandled_handler_error_caused_by_its_key_and_args():
    err = Bug("LEDGER_OFFLINE", ledger="main", tries=2)
    assert (err.code, err.grpc) == (
        "ROUTER_REASON_UNHANDLED_HANDLER_ERROR",
        GrpcCode.INTERNAL,
    )
    assert err.extras == {
        "cause": "LEDGER_OFFLINE",
        "cause.ledger": "main",
        "cause.tries": "2",
    }


@pytest.mark.parametrize(
    "status",
    [GrpcCode.UNAVAILABLE, GrpcCode.DEADLINE_EXCEEDED, GrpcCode.RESOURCE_EXHAUSTED],
)
def test_a_retryable_bug_goes_out_under_the_status_it_picks(status):
    err = RetryableBug("LEDGER_OFFLINE", status, ledger="main")
    assert (err.code, err.grpc) == ("ROUTER_REASON_UNHANDLED_HANDLER_ERROR", status)
    assert err.extras == {"cause": "LEDGER_OFFLINE", "cause.ledger": "main"}


@pytest.mark.parametrize(
    "status", [GrpcCode.INTERNAL, GrpcCode.ABORTED, GrpcCode.UNIMPLEMENTED]
)
def test_a_retryable_bug_outside_the_retryable_statuses_is_refused(status):
    with pytest.raises(ValueError, match="a retryable bug's status"):
        RetryableBug("X", status)


def test_a_typed_refusal_carries_its_object_after_its_error_info():
    refusal = order_pb2.ItemOutOfStock(item="apple", on_hand=0)
    for err in (Refused(refusal), Refused(pack(refusal))):
        assert (err.code, err.grpc) == (
            "ROUTER_REASON_COMMAND_REFUSED",
            GrpcCode.FAILED_PRECONDITION,
        )
        assert err.extras == {"refusal_type": "orders.v1.ItemOutOfStock"}
        assert list(err.status().details)[1:] == [pack(refusal)]


def test_an_undecodable_payload_names_its_message_type_and_the_runtimes_text():
    err = any_decode_error(
        "example.com/types/test.counter.IncreaseBy", ValueError("bad wire")
    )
    assert (err.code, err.grpc) == (
        "ROUTER_REASON_ANY_DECODE_FAILED",
        GrpcCode.INTERNAL,
    )
    assert err.extras == {
        "message_type": "test.counter.IncreaseBy",
        "ext_err_txt": "bad wire",
    }


def test_pack_uses_the_bare_slash_prefix():
    packed = pack(counter_pb2.IncreaseBy(n=2))
    assert packed.type_url == "/test.counter.IncreaseBy"
    assert counter_pb2.IncreaseBy.FromString(packed.value).n == 2


# --- what escapes business code ----------------------------------------------


def _aggregate(handler, applier=None) -> Router:
    rebuilder = Rebuilder(counter_pb2.CounterState)
    if applier is not None:
        rebuilder.apply(_INCREASED, applier)
    router = Router()
    router.register_aggregate(
        AggregateDispatch("Counter", "counter", rebuilder).on_command(
            _INCREASE_BY, handler
        )
    )
    return router


def _command(*prior: int) -> types_pb2.ContextualCommand:
    request = types_pb2.ContextualCommand()
    request.command.cover.CopyFrom(cover_of("counter", "c-1"))
    request.command.page.command.payload.CopyFrom(pack(counter_pb2.IncreaseBy(n=1)))
    request.events.pages.extend(
        types_pb2.EventPage(payload=pack(counter_pb2.Increased()), sequence=s)
        for s in prior
    )
    request.events.next_sequence = len(prior)
    return request


def _raising(exc):
    def handler(*_args):
        raise exc

    return handler


@pytest.mark.parametrize(
    ("escaped", "extras"),
    [
        (UnclassifiedError("socket gone"), {}),
        (KeyError("socket gone"), {"cause": "ROUTER_REASON_HANDLER_PANICKED"}),
    ],
)
def test_what_escapes_is_logged_never_answered(caplog, escaped, extras):
    with caplog.at_level(logging.ERROR, "angzarr_client.router.errors"):
        err = _raises(_aggregate(_raising(escaped)).dispatch, _command())
    assert (err.code, err.grpc, err.extras) == (
        "ROUTER_REASON_UNHANDLED_HANDLER_ERROR",
        GrpcCode.INTERNAL,
        extras,
    )
    [record] = caplog.records
    assert (record.msg, record.args, record.exc_info[1]) == (
        "handler_escaped",
        (),
        escaped,
    )
    assert "socket gone" not in str(err)


def test_a_coded_error_escapes_as_itself():
    rejection = Rejection("VALUE_NOT_POSITIVE")
    assert _raises(_aggregate(_raising(rejection)).dispatch, _command()) is rejection


def test_an_interrupt_is_not_answered():
    with pytest.raises(KeyboardInterrupt):
        _aggregate(_raising(KeyboardInterrupt())).dispatch(_command())


@pytest.mark.parametrize(
    ("raised", "cause"),
    [
        (UnclassifiedError("x"), {}),
        (RuntimeError("x"), {"cause": "ROUTER_REASON_HANDLER_PANICKED"}),
        (
            Bug("LEDGER_OFFLINE", ledger="main"),
            {"cause": "LEDGER_OFFLINE", "cause.ledger": "main"},
        ),
        (Rejection("NOPE", n=1), {"cause": "NOPE", "cause.n": "1"}),
    ],
)
def test_a_failing_applier_fails_the_fold_naming_the_page_and_its_cause(raised, cause):
    router = _aggregate(lambda *_: None, _raising(raised))
    err = _raises(router.dispatch, _command(0, 1))
    assert (err.code, err.grpc) == ("ROUTER_REASON_FOLD_FAILED", GrpcCode.INTERNAL)
    assert err.extras == {
        "sequence": "0",
        "type_url": "/test.counter.Increased",
        "state_type_url": "/test.counter.CounterState",
        **cause,
    }


def test_an_undecodable_starting_state_is_a_corrupt_persisted_event():
    request = _command()
    request.events.snapshot.state.type_url = "/test.counter.CounterState"
    request.events.snapshot.state.value = b"\xff\xff\xff"
    err = _raises(_aggregate(lambda *_: None).dispatch, request)
    assert (err.code, err.grpc, err.extras) == (
        "ROUTER_REASON_PERSISTED_EVENT_CORRUPT",
        GrpcCode.INTERNAL,
        {"type_url": "/test.counter.CounterState"},
    )


def test_a_handler_answering_none_records_nothing_but_still_answers_its_state():
    resp = _aggregate(lambda *_: None, lambda s, _e: None).dispatch(_command(0))
    assert list(resp.events.pages) == []
    assert resp.events.snapshot.state == pack(counter_pb2.CounterState())
    assert not resp.HasField("conflict")


# --- what business code is handed ---------------------------------------------


def test_a_command_handler_sees_its_input_and_runs_under_its_page_context():
    seen = []

    def handler(cmd, state, ctx):
        seen.append((cmd, state.count, ctx, current_page(), current_cover()))

    request = _command(0, 1)
    request.command.page.command_id.value = b"c" * 16
    request.command.page.correlation_id.value = b"r" * 16
    request.command.page.fact_origin.description = "stripe"
    _aggregate(handler, lambda s, _e: setattr(s, "count", s.count + 1)).dispatch(
        request
    )
    [(cmd, count, ctx, page, cover)] = seen
    assert cmd == request.command.page.command.payload
    assert count == 2
    assert (ctx.next_sequence, ctx.had_prior_events, ctx.expected_sequence) == (
        2,
        True,
        2,
    )
    assert ctx.cover == cover == request.command.cover
    assert page == PageContext(request.command.cover)
    assert ctx.command_id.value == b"c" * 16
    assert ctx.correlation_id.value == b"r" * 16
    assert ctx.fact_origin.description == "stripe"
    assert ctx.recorded_at is None
    assert ctx.conflict is None


def test_a_book_context_defaults_what_its_book_leaves_unset():
    ctx = book_context(types_pb2.CommandBook(), 7)
    assert (ctx.cover, ctx.command_id, ctx.correlation_id, ctx.fact_origin) == (
        None,
    ) * 4
    assert (ctx.next_sequence, ctx.expected_sequence, ctx.had_prior_events) == (
        7,
        7,
        False,
    )
    book = types_pb2.CommandBook()
    book.handling.expected_sequence = 0
    assert book_context(book, 7).expected_sequence == 0


def test_a_snapshot_alone_is_prior_history():
    seen = []
    request = _command()
    request.events.snapshot.state.CopyFrom(pack(counter_pb2.CounterState(count=4)))
    _aggregate(
        lambda _c, s, ctx: seen.append((s.count, ctx.had_prior_events))
    ).dispatch(request)
    assert seen == [(4, True)]


def test_appliers_see_each_page_context_and_meta():
    seen = []
    rebuilder = Rebuilder(counter_pb2.CounterState).apply_with_context(
        _INCREASED, lambda _s, _e, ctx: seen.append((ctx, current_page()))
    )
    router = Router()
    router.register_aggregate(
        AggregateDispatch("C", "counter", rebuilder).on_command(
            _INCREASE_BY, lambda *_: None
        )
    )
    request = _command(4)
    request.events.cover.CopyFrom(cover_of("counter", "c-1"))
    request.events.pages[0].meta.rules_version = "1.0"
    router.dispatch(request)
    [(ctx, page)] = seen
    assert ctx == page
    assert (ctx.cover, ctx.sequence, ctx.meta.rules_version) == (
        request.events.cover,
        4,
        "1.0",
    )


def test_outside_a_dispatch_there_is_no_page_context():
    with pytest.raises(
        RuntimeError, match="^no angzarr dispatch is being handled here$"
    ):
        current_page()
    with pytest.raises(
        RuntimeError, match="^no angzarr dispatch is being handled here$"
    ):
        current_cover()


def test_a_handler_whose_book_has_no_cover_has_no_current_cover():
    seen = []

    def handler(*_args):
        with pytest.raises(
            RuntimeError, match="^the book being handled carries no cover$"
        ) as exc:
            current_cover()
        seen.append(exc.value)

    request = _command()
    request.command.ClearField("cover")
    _aggregate(handler).dispatch(request)
    assert len(seen) == 1


def test_a_conflict_reports_the_nonce_it_was_settled_with():
    conflict = Conflict(iter([1, 2]), bytearray(b"n-1"))
    assert (conflict.window, conflict.nonce, conflict.resolved_nonce) == (
        [1, 2],
        b"n-1",
        b"",
    )
    conflict.resolve_conflicts(conflict.window, bytearray(b"n-2"))
    assert conflict.resolved_nonce == b"n-2"
    assert isinstance(conflict.resolved_nonce, bytes)


def test_a_fact_record_is_the_fact_then_its_flags_as_events():
    fact = pack(counter_pb2.Increased())
    record = FactRecord(fact, [counter_pb2.CounterState(count=1)])
    assert record.flags == (counter_pb2.CounterState(count=1),)
    assert [p.payload for p in record.book().pages] == [
        fact,
        pack(counter_pb2.CounterState(count=1)),
    ]
    assert FactRecord.as_received(fact) == FactRecord(fact)
    assert FactRecord.as_received(fact).flags == ()


def test_a_commutative_resolver_failing_is_a_handler_bug():
    router = Router()
    router.register_aggregate(
        AggregateDispatch("C", "counter", Rebuilder(counter_pb2.CounterState))
        .on_command(_INCREASE_BY, lambda *_: None)
        .with_commutative(_raising(RuntimeError("no")))
    )
    request = _command()
    request.command.handling.conflict_resolution = (
        types_pb2.CONFLICT_RESOLUTION_COMMUTATIVE
    )
    err = _raises(router.dispatch, request)
    assert err.extras == {"cause": "ROUTER_REASON_HANDLER_PANICKED"}


def test_a_refused_command_this_component_sent_is_recorded_by_its_handler():
    seen = []

    def on_refused(refused, state, ctx):
        seen.append((refused, state.count, ctx.cover))
        return FactRecord(pack(refused), [counter_pb2.CounterState(count=9)])

    router = Router()
    router.register_aggregate(
        AggregateDispatch(
            "C", "counter", Rebuilder(counter_pb2.CounterState)
        ).on_refused("test.counter.Reserve", on_refused)
    )
    refused = types_pb2.CommandRefused()
    refused.command.page.command.payload.CopyFrom(pack(counter_pb2.Reserve()))
    request = _command()
    request.command.page.fact.payload.CopyFrom(pack(refused))
    resp = router.dispatch(request)
    assert seen == [(refused, 0, request.command.cover)]
    assert [p.payload for p in resp.events.pages] == [
        pack(refused),
        pack(counter_pb2.CounterState(count=9)),
    ]


# --- policies and projectors ---------------------------------------------------


def test_a_policy_handler_registered_with_on_event_reads_the_source_cover():
    seen = []
    policy = PolicyDispatch("P", "order", "inventory").on_event(
        _INCREASED,
        lambda event, cover: seen.append((event, cover, current_page().sequence)) or [],
    )
    router = Router()
    router.register_policy(policy)
    book = types_pb2.EventBook(cover=cover_of("order", "o-1"))
    book.pages.add(
        sequence=3, payload=pack(counter_pb2.Increased())
    ).meta.rules_version = "2"
    resp = router.dispatch_policy(book)
    assert seen == [(book.pages[0].payload, book.cover, 3)]
    assert [(r.trigger_sequence, list(r.commands)) for r in resp.reactions] == [(3, [])]


def test_a_policy_handler_sees_its_trigger_meta_and_none_for_a_bookless_cover():
    seen = []
    policy = PolicyDispatch("P", "order", "inventory").on_event_with_context(
        _INCREASED, lambda _e, ctx: seen.append(ctx)
    )
    router = Router()
    router.register_policy(policy)
    book = types_pb2.EventBook()
    book.pages.add(sequence=1, payload=pack(counter_pb2.Increased()))
    book.pages.add(
        sequence=2, payload=pack(counter_pb2.Increased())
    ).meta.rules_version = "2"
    router.dispatch_policy(book)
    assert [(c.cover, c.sequence, c.meta) for c in seen] == [
        (None, 1, None),
        (None, 2, book.pages[1].meta),
    ]


def test_a_translating_callback_may_answer_an_any():
    fact = pack(order_pb2.StockUnavailable(item="apple"))
    policy = PolicyDispatch("P", "order", "inventory").on_refused(
        "orders.v1.ReserveStock", lambda _r, _c: fact
    )
    router = Router()
    router.register_policy(policy)
    refusal = policy_pb2.Refusal()
    refusal.command.page.command.payload.CopyFrom(pack(order_pb2.ReserveStock()))
    assert router.dispatch_refusal(refusal).fact.page.fact.payload == fact


def test_a_failing_translating_callback_is_a_handler_bug():
    policy = PolicyDispatch("P", "order", "inventory").on_refused(
        "orders.v1.ReserveStock", _raising(UnclassifiedError("x"))
    )
    router = Router()
    router.register_policy(policy)
    refusal = policy_pb2.Refusal()
    refusal.command.page.command.payload.CopyFrom(pack(order_pb2.ReserveStock()))
    assert (
        _raises(router.dispatch_refusal, refusal).code
        == "ROUTER_REASON_UNHANDLED_HANDLER_ERROR"
    )


def _projector(*domains: str) -> Router:
    projector = ProjectorDispatch("Sum", wrappers_pb2.Int64Value).on_event(
        "google.protobuf.Int64Value",
        lambda p, e: setattr(
            p, "value", p.value + wrappers_pb2.Int64Value.FromString(e.value).value
        ),
    )
    if domains:
        projector.for_domains(*domains)
    router = Router()
    router.register_projector(projector)
    return router


def _values(domain: str, *values: int, start: int = 0) -> types_pb2.EventBook:
    book = types_pb2.EventBook(cover=types_pb2.Cover(domain=domain), next_sequence=9)
    for seq, value in enumerate(values, start):
        book.pages.add(sequence=seq, payload=pack(wrappers_pb2.Int64Value(value=value)))
    return book


def test_a_projector_answers_its_position_and_projection():
    projection = _projector().dispatch_projector(_values("any", 2, 3, start=4))
    assert projection == types_pb2.Projection(
        cover=types_pb2.Cover(domain="any"),
        projector="Sum",
        next_sequence=6,
        projection=pack(wrappers_pb2.Int64Value(value=5)),
    )


def test_an_empty_book_leaves_the_projector_at_the_books_next_sequence():
    assert _projector().dispatch_projector(_values("any")).next_sequence == 9


def test_a_page_with_no_applier_is_passed_over_and_the_fold_goes_on():
    book = types_pb2.EventBook(cover=types_pb2.Cover(domain="any"))
    book.pages.add(sequence=0, payload=pack(wrappers_pb2.StringValue(value="x")))
    book.pages.add(sequence=1, payload=pack(wrappers_pb2.Int64Value(value=7)))
    projection = _projector().dispatch_projector(book)
    assert wrappers_pb2.Int64Value.FromString(projection.projection.value).value == 7


@pytest.mark.parametrize(
    "dispatch",
    [
        PolicyDispatch("P", "order", "stock"),
        ProjectorDispatch("Sum", wrappers_pb2.Int64Value),
    ],
)
def test_a_policy_or_projector_takes_the_build_version_it_is_given(dispatch):
    assert dispatch.with_rules_version("1.4.2") is dispatch
    assert dispatch.rules_version == "1.4.2"


@pytest.mark.parametrize(
    ("domains", "domain", "folded"),
    [
        (("a", "b"), "b", 7),
        (("a", "b"), "c", 0),
        (("a", WILDCARD_DOMAIN), "c", 7),
    ],
)
def test_a_projector_folds_only_its_domains(domains, domain, folded):
    projection = _projector(*domains).dispatch_projector(_values(domain, 7))
    assert (
        wrappers_pb2.Int64Value.FromString(projection.projection.value).value == folded
    )


# --- the router ----------------------------------------------------------------


def test_a_router_hosting_nothing_refuses_every_delivery():
    router = Router()
    for call in (
        lambda: router.dispatch(types_pb2.ContextualCommand()),
        lambda: router.dispatch_policy(types_pb2.EventBook()),
        lambda: router.dispatch_refusal(policy_pb2.Refusal()),
        lambda: router.dispatch_projector(types_pb2.EventBook()),
    ):
        err = _raises(call)
        assert (err.code, err.grpc) == (
            "ROUTER_REASON_NO_HANDLERS_REGISTERED",
            GrpcCode.INTERNAL,
        )


@pytest.mark.parametrize(
    "call", ["dispatch", "dispatch_policy", "dispatch_refusal", "dispatch_projector"]
)
def test_a_delivery_for_another_kind_is_unimplemented(call):
    kinds = {
        "dispatch": ProjectorDispatch("S", wrappers_pb2.Int64Value),
        "dispatch_policy": AggregateDispatch(
            "A", "a", Rebuilder(counter_pb2.CounterState)
        ),
        "dispatch_refusal": ProcessManagerDispatch(
            "M", "m", Rebuilder(counter_pb2.CounterState)
        ),
        "dispatch_projector": PolicyDispatch("P", "o", "i"),
    }
    router = Router()
    router.register_aggregate(kinds[call])
    err = _raises(getattr(router, call), None)
    assert (err.code, err.grpc, err.extras) == (
        "ROUTER_REASON_NO_COMPONENT_OF_KIND",
        GrpcCode.UNIMPLEMENTED,
        {},
    )


def test_a_process_manager_is_dispatched_as_a_domain():
    router = Router()
    router.register_process_manager(
        ProcessManagerDispatch("M", "m", Rebuilder(counter_pb2.CounterState))
        .on_command(_INCREASE_BY, lambda *_: None)
        .with_rules_version("3")
    )
    assert router.dispatch(_command()).rules_version == "3"


def test_a_closed_router_hosts_nothing():
    with Router() as router:
        router.register_aggregate(
            AggregateDispatch("A", "a", Rebuilder(counter_pb2.CounterState))
        )
    assert (
        _raises(router.dispatch, _command()).code
        == "ROUTER_REASON_NO_HANDLERS_REGISTERED"
    )
    router.register_projector(ProjectorDispatch("S", wrappers_pb2.Int64Value))


@pytest.mark.parametrize(
    ("first", "kind"),
    [
        (PolicyDispatch("P", "o", "i"), "POLICY"),
        (
            ProcessManagerDispatch("M", "m", Rebuilder(counter_pb2.CounterState)),
            "PROCESS_MANAGER",
        ),
        (ProjectorDispatch("S", wrappers_pb2.Int64Value), "PROJECTOR"),
    ],
)
def test_a_second_component_is_refused_naming_the_first(first, kind):
    router = Router()
    router.register_policy(first)
    err = _raises(
        router.register_aggregate,
        AggregateDispatch("A", "a", Rebuilder(counter_pb2.CounterState)),
    )
    assert (err.code, err.grpc, err.extras) == (
        "ROUTER_REASON_DUPLICATE_COMPONENT",
        GrpcCode.INTERNAL,
        {"component": first.name, "component_kind": kind},
    )


def test_the_router_describes_nothing():
    # Decision 14.42: the descriptor is angzarr-cli's; the router holds no
    # descriptor logic.
    assert not hasattr(Router(), "component_descriptor")


def test_an_any_payload_needs_no_packing():
    payload = any_pb2.Any(type_url="/x.Y", value=b"")
    record = FactRecord(payload)
    assert record.book().pages[0].payload is not payload
    assert record.book().pages[0].payload == payload


def test_a_typed_refusal_is_named_by_its_type_whatever_its_prefix():
    refusal = any_pb2.Any(type_url="example.com/types/orders.v1.ItemOutOfStock")
    assert Refused(refusal).extras == {"refusal_type": "orders.v1.ItemOutOfStock"}


def test_an_undecodable_message_carries_the_runtimes_own_text():
    from google.protobuf.message import DecodeError

    from angzarr_client.router.context import decode

    with pytest.raises(DecodeError) as runtime:
        types_pb2.CommandRefused.FromString(b"\xff\xff\xff")
    err = _raises(decode, types_pb2.CommandRefused, b"\xff\xff\xff")
    assert err.extras == {
        "message_type": "io.angzarr.v1.CommandRefused",
        "ext_err_txt": str(runtime.value),
    }


def test_an_applier_registered_with_apply_is_handed_the_event():
    seen = []
    router = _aggregate(lambda *_: None, lambda _s, event: seen.append(event))
    request = _command(0)
    router.dispatch(request)
    assert seen == [request.events.pages[0].payload]


def test_an_input_with_no_body_is_an_unknown_command_type():
    request = _command()
    request.command.page.ClearField("command")
    err = _raises(_aggregate(lambda *_: None).dispatch, request)
    assert (err.code, err.extras) == (
        "ROUTER_REASON_UNKNOWN_COMMAND_TYPE",
        {"type_url": ""},
    )


def _covers_seen(request) -> list:
    seen = []
    rebuilder = Rebuilder(counter_pb2.CounterState).apply_with_context(
        _INCREASED, lambda _s, _e, ctx: seen.append((ctx.cover, ctx.sequence))
    )
    router = Router()
    router.register_aggregate(
        AggregateDispatch("C", "counter", rebuilder).on_command(
            _INCREASE_BY,
            lambda *_: types_pb2.EventBook(
                pages=[types_pb2.EventPage(payload=pack(counter_pb2.Increased()))]
            ),
        )
    )
    router.dispatch(request)
    return seen


def test_every_page_folds_under_its_books_cover_or_none():
    request = _command(0, 1)
    request.command.handling.expected_sequence = 1
    assert _covers_seen(request) == [(None, 0), (None, 1), (None, 2)]
    request.events.cover.CopyFrom(cover_of("counter", "c-1"))
    cover = request.events.cover
    assert _covers_seen(request) == [(cover, 0), (cover, 1), (cover, 2)]


def test_a_page_after_the_expected_sequence_failing_is_a_persisted_fold_failure():
    request = _command(0, 1)
    request.command.handling.expected_sequence = 0
    router = _aggregate(lambda *_: None, _raising(UnclassifiedError("x")))
    assert _raises(router.dispatch, request).code == "ROUTER_REASON_FOLD_FAILED"


def test_a_page_with_no_applier_does_not_stop_the_fold():
    seen = []
    request = _command(1)
    request.events.pages.insert(
        0, types_pb2.EventPage(payload=pack(counter_pb2.Reserve()))
    )
    _aggregate(
        lambda _c, s, _x: seen.append(s.count), lambda s, _e: setattr(s, "count", 5)
    ).dispatch(request)
    assert seen == [5]


def test_the_resolver_runs_under_an_empty_page_context():
    seen = []

    def changed(_before, _after):
        seen.append(current_page())
        return []

    router = Router()
    router.register_aggregate(
        AggregateDispatch("C", "counter", Rebuilder(counter_pb2.CounterState))
        .on_command(_INCREASE_BY, lambda *_: None)
        .with_commutative(changed)
    )
    request = _command()
    request.command.handling.conflict_resolution = (
        types_pb2.CONFLICT_RESOLUTION_COMMUTATIVE
    )
    router.dispatch(request)
    assert seen == [PageContext(), PageContext()]


def test_a_translating_callback_runs_under_the_refused_commands_cover():
    seen = []

    def translate(_refusal, ctx):
        seen.append((current_cover(), ctx.next_sequence, ctx.expected_sequence))
        return order_pb2.StockUnavailable()

    router = Router()
    router.register_policy(
        PolicyDispatch("P", "order", "inventory").on_refused(
            "orders.v1.ReserveStock", translate
        )
    )
    refusal = policy_pb2.Refusal()
    refusal.command.cover.CopyFrom(cover_of("inventory", "i-1"))
    refusal.command.page.command.payload.CopyFrom(pack(order_pb2.ReserveStock()))
    router.dispatch_refusal(refusal)
    assert seen == [(refusal.command.cover, 0, 0)]


def test_a_projector_fold_failing_is_a_fold_failure_and_sees_no_cover_when_none():
    seen = []

    def fold(_p, _e, ctx):
        seen.append(ctx.cover)
        raise UnclassifiedError("x")

    router = Router()
    router.register_projector(
        ProjectorDispatch("S", wrappers_pb2.Int64Value).on_event_with_context(
            "google.protobuf.Int64Value", fold
        )
    )
    book = _values("", 1)
    book.ClearField("cover")
    err = _raises(router.dispatch_projector, book)
    assert (err.code, seen) == ("ROUTER_REASON_FOLD_FAILED", [None])
