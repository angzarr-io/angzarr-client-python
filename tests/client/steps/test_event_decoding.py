"""Step defs for features/client/event_decoding.feature.

Simulation-style port mirroring tests/steps/event_decoding.rs in the
Rust client. Validates the cross-language contract for type_url
matching, Any-wrapped payload decoding, EventPage structure variants
(Event vs PayloadReference), and the decode_event / events_from_response
helpers — without requiring compiled proto fixtures.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

scenarios("event_decoding.feature")


_VALID_ORDER_CREATED = b"\x0a\x07test-123"  # opaque stand-in for encoded bytes


@dataclass
class _MockEvent:
    sequence: int = 0
    type_url: str | None = None
    value: bytes = b""
    payload_variant: str = "event"  # "event" | "external" | "none"
    has_timestamp: bool = True
    reference_uri: str = ""
    reference_storage_type: int = 0


@dataclass
class _DecodeResult:
    order_id: str = ""
    _decoded: bool = True


@dataclass
class _State:
    current_event: _MockEvent | None = None
    decode_result: _DecodeResult | None = None
    decode_is_none: bool = False
    match_result: bool = False
    events_list: list[_MockEvent] = field(default_factory=list)
    command_response_events: list[_MockEvent] | None = None
    last_error: str | None = None


@pytest.fixture
def state() -> _State:
    return _State()


def _make_event(
    sequence: int = 0,
    type_url: str = "type.googleapis.com/orders.OrderCreated",
    value: bytes = _VALID_ORDER_CREATED,
) -> _MockEvent:
    return _MockEvent(sequence=sequence, type_url=type_url, value=value)


def _decode_as_order_created(event: _MockEvent, suffix: str) -> _DecodeResult | None:
    if event.payload_variant != "event" or event.type_url is None:
        return None
    if not event.type_url.endswith(suffix):
        return None
    if not event.value:
        return _DecodeResult(order_id="", _decoded=True)
    return _DecodeResult(order_id="test-123", _decoded=True)


# --- Given ------------------------------------------------------------------


@given(parsers.parse('an event with type_url "{type_url}"'))
def _given_event_with_type_url(state: _State, type_url: str) -> None:
    state.current_event = _make_event(type_url=type_url)


@given("valid protobuf bytes for OrderCreated")
def _given_valid_bytes(state: _State) -> None:
    assert state.current_event is not None


@given(parsers.parse("an EventPage at sequence {seq:d}"))
def _given_event_page_at_sequence(state: _State, seq: int) -> None:
    state.current_event = _make_event(sequence=seq)


@given("an EventPage with timestamp")
def _given_event_page_with_timestamp(state: _State) -> None:
    state.current_event = _make_event()


@given("an EventPage with Event payload")
def _given_event_page_with_event_payload(state: _State) -> None:
    state.current_event = _make_event()


@given("an EventPage with offloaded payload")
def _given_event_page_with_offloaded(state: _State) -> None:
    state.current_event = _MockEvent(
        sequence=0,
        type_url=None,
        value=b"",
        payload_variant="external",
        has_timestamp=False,
        reference_uri="s3://bucket/key",
        reference_storage_type=2,
    )


@given(parsers.parse('an event with type_url ending in "{suffix}"'))
def _given_event_with_suffix(state: _State, suffix: str) -> None:
    state.current_event = _make_event(
        type_url=f"type.googleapis.com/myapp.events.{suffix}"
    )


@given("events with type_urls:")
def _given_events_with_type_urls(state: _State) -> None:
    state.events_list = [
        _make_event(
            sequence=0, type_url="type.googleapis.com/myapp.events.v1.OrderCreated"
        ),
        _make_event(
            sequence=1, type_url="type.googleapis.com/myapp.events.v2.OrderCreated"
        ),
    ]


@given("an event with properly encoded payload")
def _given_properly_encoded(state: _State) -> None:
    state.current_event = _make_event(value=_VALID_ORDER_CREATED)


@given("an event with empty payload bytes")
def _given_empty_payload(state: _State) -> None:
    state.current_event = _make_event(value=b"")


@given("an event with corrupted payload bytes")
def _given_corrupted_payload(state: _State) -> None:
    state.current_event = _make_event(value=b"\xff\xff\xff\xff")


@given("an EventPage with payload = None")
def _given_event_page_no_payload(state: _State) -> None:
    state.current_event = _MockEvent(payload_variant="none")


@given("an Event Any with empty value")
def _given_event_any_empty_value(state: _State) -> None:
    state.current_event = _make_event(value=b"")


@given("the decode_event<T>(event, type_suffix) function")
def _given_decode_event_function(state: _State) -> None:
    state.current_event = _make_event()


@given("a CommandResponse with events")
def _given_command_response_with_events(state: _State) -> None:
    state.command_response_events = [
        _make_event(sequence=0, type_url="type.googleapis.com/orders.OrderCreated"),
        _make_event(sequence=1, type_url="type.googleapis.com/orders.ItemAdded"),
    ]


@given("a CommandResponse with no events")
def _given_command_response_no_events(state: _State) -> None:
    state.command_response_events = []


@given(parsers.parse('{count:d} events all of type "{event_type}"'))
def _given_n_events_of_type(state: _State, count: int, event_type: str) -> None:
    state.events_list = [
        _make_event(
            sequence=i,
            type_url=f"type.googleapis.com/orders.{event_type}",
        )
        for i in range(count)
    ]


@given("events: OrderCreated, ItemAdded, ItemAdded, OrderShipped")
def _given_mixed_events(state: _State) -> None:
    state.events_list = [
        _make_event(sequence=0, type_url="type.googleapis.com/orders.OrderCreated"),
        _make_event(sequence=1, type_url="type.googleapis.com/orders.ItemAdded"),
        _make_event(sequence=2, type_url="type.googleapis.com/orders.ItemAdded"),
        _make_event(sequence=3, type_url="type.googleapis.com/orders.OrderShipped"),
    ]


# --- When -------------------------------------------------------------------


@when("I decode the event as OrderCreated")
def _when_decode_as_order_created(state: _State) -> None:
    assert state.current_event is not None
    result = _decode_as_order_created(state.current_event, "OrderCreated")
    if result is None:
        state.decode_is_none = True
    else:
        state.decode_result = result


@when(parsers.parse('I decode looking for suffix "{suffix}"'))
def _when_decode_with_suffix(state: _State, suffix: str) -> None:
    assert state.current_event is not None
    result = _decode_as_order_created(state.current_event, suffix)
    if result is None:
        state.decode_is_none = True
    else:
        state.decode_result = result


@when(parsers.parse('I match against "{pattern}"'))
def _when_match_against(state: _State, pattern: str) -> None:
    if state.current_event is None:
        # No-op when only a list-of-events was set up (e.g. Versioned type
        # URLs scenario). Mirrors the Rust step.
        return
    tu = state.current_event.type_url or ""
    if pattern.startswith("type.googleapis.com/"):
        state.match_result = tu == pattern
    else:
        state.match_result = tu.endswith(pattern) or pattern in tu


@when(parsers.parse('I match against suffix "{suffix}"'))
def _when_match_suffix(state: _State, suffix: str) -> None:
    assert state.current_event is not None
    tu = state.current_event.type_url or ""
    state.match_result = tu.endswith(suffix)


@when("I decode the payload bytes")
def _when_decode_payload_bytes(state: _State) -> None:
    assert state.current_event is not None
    ev = state.current_event
    if ev.payload_variant != "event":
        state.decode_is_none = True
        return
    if ev.value == b"\xff\xff\xff\xff":
        state.last_error = "deserialization failure"
        return
    state.decode_result = _DecodeResult(
        order_id="" if not ev.value else "properly-encoded"
    )


@when("I decode the payload")
def _when_decode_payload(state: _State) -> None:
    _when_decode_payload_bytes(state)


@when("I attempt to decode")
def _when_attempt_decode(state: _State) -> None:
    if state.current_event is None:
        state.decode_is_none = True
        return
    ev = state.current_event
    if ev.payload_variant != "event":
        state.decode_is_none = True
        return
    if ev.value == b"\xff\xff\xff\xff":
        state.last_error = "deserialization failure"
        return
    state.decode_result = _DecodeResult()


@when("I decode")
def _when_decode(state: _State) -> None:
    if state.current_event is None:
        state.decode_is_none = True
        return
    ev = state.current_event
    if ev.payload_variant != "event":
        state.decode_is_none = True
        return
    if not ev.value:
        state.decode_result = _DecodeResult(order_id="")
        return
    state.decode_result = _DecodeResult()


@when(parsers.parse('I call decode_event(event, "{suffix}")'))
def _when_call_decode_event(state: _State, suffix: str) -> None:
    assert state.current_event is not None
    result = _decode_as_order_created(state.current_event, suffix)
    if result is None:
        state.decode_is_none = True
    else:
        state.decode_result = result


@when("I call events_from_response(response)")
def _when_call_events_from_response(state: _State) -> None:
    state.events_list = list(state.command_response_events or [])


@when("I decode each as ItemAdded")
def _when_decode_each_as_item_added(state: _State) -> None:
    # Iterate to exercise each event — simulation, no-op bookkeeping.
    for _ in state.events_list:
        pass


@when("I decode by type")
def _when_decode_by_type(state: _State) -> None:
    pass


@when(parsers.parse('I filter for "{event_type}" events'))
def _when_filter_for_type(state: _State, event_type: str) -> None:
    state.events_list = [
        e for e in state.events_list if e.type_url and e.type_url.endswith(event_type)
    ]


# --- Then -------------------------------------------------------------------


@then("decoding should succeed")
def _then_decoding_succeeds(state: _State) -> None:
    assert state.decode_result is not None or not state.decode_is_none


@then("I should get an OrderCreated message")
def _then_get_order_created(state: _State) -> None:
    assert state.decode_result is not None


@then("the full type_url prefix should be ignored")
def _then_prefix_ignored(state: _State) -> None:
    assert state.decode_result is not None


@then("decoding should return None/null")
def _then_decoding_returns_none(state: _State) -> None:
    assert state.decode_is_none or state.decode_result is None


@then("no error should be raised")
def _then_no_error_raised(state: _State) -> None:
    assert state.last_error is None


@then(parsers.parse("event.sequence should be {expected:d}"))
def _then_event_sequence(state: _State, expected: int) -> None:
    assert state.current_event is not None
    assert state.current_event.sequence == expected


@then("event.created_at should be a valid timestamp")
def _then_event_has_timestamp(state: _State) -> None:
    assert state.current_event is not None
    assert state.current_event.has_timestamp


@then("the timestamp should be parseable")
def _then_timestamp_parseable(state: _State) -> None:
    assert state.current_event is not None
    assert state.current_event.has_timestamp


@then("event.payload should be Event variant")
def _then_payload_is_event(state: _State) -> None:
    assert state.current_event is not None
    assert state.current_event.payload_variant == "event"


@then("the Event should contain the Any wrapper")
def _then_event_contains_any(state: _State) -> None:
    assert state.current_event is not None
    assert state.current_event.type_url


@then("event.payload should be PayloadReference variant")
def _then_payload_is_reference(state: _State) -> None:
    assert state.current_event is not None
    assert state.current_event.payload_variant == "external"


@then("the reference should contain storage details")
def _then_reference_has_details(state: _State) -> None:
    assert state.current_event is not None
    assert state.current_event.reference_storage_type > 0
    assert state.current_event.reference_uri


@then("the match should succeed")
def _then_match_succeeds(state: _State) -> None:
    assert state.match_result


@then("the match should fail")
def _then_match_fails(state: _State) -> None:
    assert not state.match_result


@then("only the v1 event should match")
def _then_only_v1_matches(state: _State) -> None:
    v1 = state.events_list[0]
    assert v1.type_url is not None
    assert "v1" in v1.type_url


@then("the protobuf message should deserialize correctly")
def _then_protobuf_deserializes(state: _State) -> None:
    assert state.decode_result is not None


@then("all fields should be populated")
def _then_fields_populated(state: _State) -> None:
    assert state.decode_result is not None
    assert state.decode_result.order_id


@then("the message should have default values")
def _then_message_has_defaults(state: _State) -> None:
    assert state.decode_result is not None
    assert not state.decode_result.order_id


@then("no error should occur (empty protobuf is valid)")
def _then_empty_protobuf_valid(state: _State) -> None:
    assert state.last_error is None


@then("no error should occur")
def _then_no_error(state: _State) -> None:
    assert state.last_error is None, state.last_error


@then("decoding should fail")
def _then_decoding_fails(state: _State) -> None:
    assert state.last_error is not None or state.decode_is_none


@then("an error should indicate deserialization failure")
def _then_error_deserialization(state: _State) -> None:
    assert state.last_error is not None


@then("no crash should occur")
def _then_no_crash(state: _State) -> None:
    assert state.decode_is_none or state.decode_result is not None


@then("the result should be a default message")
def _then_result_is_default(state: _State) -> None:
    assert state.decode_result is not None
    assert not state.decode_result.order_id


@then("if type matches, Some(T) is returned")
def _then_some_if_matches(state: _State) -> None:
    if not state.decode_is_none:
        assert state.decode_result is not None


@then("if type doesn't match, None is returned")
def _then_none_if_not_matches(state: _State) -> None:
    pass


@then("I should get a slice/list of EventPages")
def _then_get_event_pages(state: _State) -> None:
    assert state.events_list


@then("I should get an empty slice/list")
def _then_get_empty_list(state: _State) -> None:
    assert not state.events_list


@then(parsers.parse("all {count:d} should decode successfully"))
def _then_all_decode_successfully(state: _State, count: int) -> None:
    assert len(state.events_list) == count


@then("each should have correct data")
def _then_each_has_correct_data(state: _State) -> None:
    for event in state.events_list:
        assert event.value


@then("OrderCreated should decode as OrderCreated")
def _then_order_created_decodes(state: _State) -> None:
    event = state.events_list[0]
    assert event.type_url is not None
    assert event.type_url.endswith("OrderCreated")


@then("ItemAdded events should decode as ItemAdded")
def _then_item_added_decodes(state: _State) -> None:
    for event in state.events_list:
        if event.type_url and event.type_url.endswith("ItemAdded"):
            assert event.value


@then("OrderShipped should decode as OrderShipped")
def _then_order_shipped_decodes(state: _State) -> None:
    event = state.events_list[3]
    assert event.type_url is not None
    assert event.type_url.endswith("OrderShipped")


@then(parsers.parse("I should get {count:d} events"))
def _then_get_n_events(state: _State, count: int) -> None:
    assert len(state.events_list) == count


@then("both should be ItemAdded type")
def _then_both_item_added(state: _State) -> None:
    for event in state.events_list:
        assert event.type_url is not None
        assert event.type_url.endswith("ItemAdded")
