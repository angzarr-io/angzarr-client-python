"""Tests for helper functions."""

from datetime import datetime, timezone
from uuid import UUID as PyUUID

import pytest
from google.protobuf.any_pb2 import Any as ProtoAny

from angzarr_client._pb import EventPage
from angzarr_client.errors import InvalidTimestampError
from angzarr_client.helpers import (
    CORRELATION_ID_HEADER,
    DEFAULT_EDITION,
    META_ANGZARR_DOMAIN,
    PROJECTION_DOMAIN_PREFIX,
    TYPE_URL_PREFIX,
    UNKNOWN_DOMAIN,
    decode_event,
    events_from_response,
    new_command_book,
    new_command_page,
    new_cover,
    now,
    parse_timestamp,
    proto_to_uuid,
    range_selection,
    temporal_by_sequence,
    temporal_by_time,
    type_name_from_url,
    type_url,
    type_url_matches,
    uuid_to_proto,
)


class TestConstants:
    """Tests for module constants."""

    def test_unknown_domain(self) -> None:
        assert UNKNOWN_DOMAIN == "unknown"

    def test_there_is_no_wildcard_domain(self) -> None:
        # Decision 14.53: a projector lists every domain it reads.
        import angzarr_client
        import angzarr_client.helpers
        import angzarr_client.router

        for module in (angzarr_client, angzarr_client.helpers, angzarr_client.router):
            assert not hasattr(module, "WILDCARD_DOMAIN"), module.__name__

    def test_default_edition(self) -> None:
        assert DEFAULT_EDITION == ""

    def test_meta_domain(self) -> None:
        assert META_ANGZARR_DOMAIN == "_angzarr"

    def test_projection_prefix(self) -> None:
        assert PROJECTION_DOMAIN_PREFIX == "_projection"

    def test_correlation_header(self) -> None:
        assert CORRELATION_ID_HEADER == "x-correlation-id"

    def test_type_url_prefix(self) -> None:
        assert TYPE_URL_PREFIX == "/"


# Cover/EventBook/CommandBook/Query field accessors live on the wrapper
# classes (``Cover.domain()``, ``EventBook.next_sequence()``, etc.); see
# ``test_wrappers.py``. The free-function accessors that used to live
# here were deleted along with the W-suffix wrapper aliases.


class TestUuidConversion:
    """Tests for UUID conversion functions."""

    def test_round_trip(self) -> None:
        """UUID can round-trip through proto."""
        original = PyUUID("deadbeef-dead-beef-dead-beefdeadbeef")
        proto = uuid_to_proto(original)
        result = proto_to_uuid(proto)
        assert result == original

    def test_uuid_to_proto_bytes(self) -> None:
        """uuid_to_proto sets correct bytes."""
        test_uuid = PyUUID("12345678-1234-5678-1234-567812345678")
        proto = uuid_to_proto(test_uuid)
        assert proto.value == test_uuid.bytes


# EventBook accessor free functions (next_sequence, event_pages) were
# deleted; see ``EventBook`` wrapper in ``test_wrappers.py``.


class TestEventsFromResponse:
    """Tests for events_from_response function."""

    def test_returns_none_for_none_response(self) -> None:
        """Returns empty list for None response."""
        assert events_from_response(None) == []

    def test_returns_empty_for_no_events_field(self) -> None:
        """Returns empty list when events field not set."""
        from angzarr_client._pb import CommandResponse

        resp = CommandResponse()
        assert events_from_response(resp) == []

    def test_returns_pages_when_present(self) -> None:
        """Returns event pages when present."""
        from angzarr_client._pb import CommandResponse

        resp = CommandResponse()
        resp.events.pages.add().sequence = 1
        resp.events.pages.add().sequence = 2
        result = events_from_response(resp)
        assert len(result) == 2


class TestTypeUrlHelpers:
    """Tests for type URL helper functions."""

    def test_type_url_construction(self) -> None:
        """type_url constructs full URL from a fully-qualified type name.

        Audit finding #32 (Option A): single-arg signature matching Rust."""
        result = type_url("com.example.MyMessage")
        assert result == "/com.example.MyMessage"

    def test_type_url_is_slash_plus_the_proto_full_name(self) -> None:
        """The framework protos carry their proto package (io.angzarr.v1)
        on the wire; ``type_url`` is "/" + that full name."""
        result = type_url("io.angzarr.v1.Cover")
        assert result == "/io.angzarr.v1.Cover"

    def test_wire_name_is_identity_in_python(self) -> None:
        """``wire_name`` is identity in Python — Python's protoc emits
        the full proto package on the wire, so there's no prefix to
        strip. The function exists for cross-language API symmetry
        with Rust's ``convert::wire_name``."""
        from angzarr_client.helpers import wire_name

        assert wire_name("examples.OrderCreated") == "examples.OrderCreated"
        assert wire_name("io.angzarr.v1.Cover") == "io.angzarr.v1.Cover"

    def test_type_name_from_url_fully_qualified(self) -> None:
        """type_name_from_url extracts fully qualified name after last slash."""
        result = type_name_from_url("type.googleapis.com/com.example.MyMessage")
        assert result == "com.example.MyMessage"

    def test_type_name_from_url_with_slash(self) -> None:
        """type_name_from_url extracts name after last slash."""
        result = type_name_from_url("prefix/MyMessage")
        assert result == "MyMessage"

    def test_type_name_from_url_plain(self) -> None:
        """type_name_from_url returns input if no separators."""
        result = type_name_from_url("MyMessage")
        assert result == "MyMessage"

    def test_type_url_matches_true(self) -> None:
        """type_url_matches returns True for exact match."""
        assert (
            type_url_matches(
                "type.googleapis.com/com.example.OrderCreated",
                "com.example.OrderCreated",
            )
            is True
        )

    def test_type_url_matches_false(self) -> None:
        """type_url_matches returns False for non-matching type name."""
        assert (
            type_url_matches(
                "type.googleapis.com/com.example.OrderCreated",
                "com.example.OrderCanceled",
            )
            is False
        )

    def test_type_url_matches_any_prefix_by_name(self) -> None:
        """Any prefix names the message after its last "/"."""
        for url in (
            "/examples.OrderCreated",
            "type.googleapis.com/examples.OrderCreated",
            "example.com/types/examples.OrderCreated",
            "examples.OrderCreated",
        ):
            assert type_url_matches(url, "examples.OrderCreated") is True

    def test_type_url_matches_distinguishes_versions(self) -> None:
        assert (
            type_url_matches("/examples.v2.OrderCreated", "examples.v1.OrderCreated")
            is False
        )

    def test_type_url_matches_post_normalize_is_exact(self) -> None:
        """Post-normalize, the comparison is exact (no suffix matching)."""
        assert (
            type_url_matches(
                "type.googleapis.com/examples.OrderCreated",
                "OrderCreated",
            )
            is False
        )


class TestTimestampHelpers:
    """Tests for timestamp helper functions."""

    def test_now_returns_timestamp(self) -> None:
        """now returns a Timestamp with current time."""
        before = datetime.now(timezone.utc)
        ts = now()
        after = datetime.now(timezone.utc)
        # Timestamp should be between before and after
        ts_datetime = ts.ToDatetime(tzinfo=timezone.utc)
        assert before <= ts_datetime <= after

    def test_parse_timestamp_valid(self) -> None:
        """parse_timestamp parses valid RFC3339."""
        ts = parse_timestamp("2024-01-15T10:30:00Z")
        assert ts.seconds > 0

    def test_parse_timestamp_with_nanos(self) -> None:
        """parse_timestamp handles nanoseconds."""
        ts = parse_timestamp("2024-01-15T10:30:00.123456789Z")
        assert ts.nanos > 0

    def test_parse_timestamp_invalid_raises(self) -> None:
        """parse_timestamp raises InvalidTimestampError for invalid input."""
        with pytest.raises(InvalidTimestampError):
            parse_timestamp("not-a-timestamp")

    def test_parse_timestamp_error_carries_parser_message(self) -> None:
        """The InvalidTimestampError message is the underlying parser error."""
        with pytest.raises(InvalidTimestampError) as exc:
            parse_timestamp("not-a-timestamp")
        cause = exc.value.__cause__
        assert isinstance(cause, ValueError)
        assert exc.value.message == str(cause)


class TestDecodeEvent:
    """Tests for decode_event function."""

    def test_returns_none_for_none_page(self) -> None:
        """Returns None for None page."""
        from angzarr_client._pb import Cover

        assert decode_event(None, "Cover", Cover) is None

    def test_returns_none_for_no_event_field(self) -> None:
        """Returns None when event field not set."""
        from angzarr_client._pb import Cover

        page = EventPage(sequence=1)
        assert decode_event(page, "Cover", Cover) is None

    def test_returns_none_for_type_mismatch(self) -> None:
        """Returns None when type URL doesn't match."""
        from angzarr_client._pb import Cover

        page = EventPage(sequence=1)
        page.payload.type_url = "type.googleapis.com/some.OtherType"
        page.payload.value = b""
        assert decode_event(page, "Cover", Cover) is None

    def test_returns_decoded_message(self) -> None:
        """Returns decoded message when type matches."""
        from angzarr_client._pb import Cover

        # Create a cover and pack it
        cover = Cover(domain="test", edition="abc")
        page = EventPage(sequence=1)
        page.payload.Pack(cover)

        # Use full type name for exact matching
        result = decode_event(page, "io.angzarr.v1.Cover", Cover)
        assert result is not None
        assert result.domain == "test"
        assert result.edition == "abc"

    def test_returns_none_for_decode_failure(self) -> None:
        """Returns None when decoding fails."""
        from angzarr_client._pb import Cover

        # Create page with matching type URL but invalid data
        page = EventPage(sequence=1)
        page.payload.type_url = "type.googleapis.com/angzarr.Cover"
        page.payload.value = b"invalid proto data that will fail to decode"
        # Should return None, not raise
        assert decode_event(page, "Cover", Cover) is None


class TestConstructionHelpers:
    """Tests for construction helper functions."""

    def test_new_cover_minimal(self) -> None:
        """new_cover creates cover with required fields."""
        root = PyUUID("12345678-1234-5678-1234-567812345678")
        cover = new_cover("orders", root)
        assert cover.domain == "orders"
        assert proto_to_uuid(cover.root) == root
        assert not cover.HasField("edition")

    def test_new_cover_with_edition(self) -> None:
        """new_cover names the edition when given one."""
        root = PyUUID("12345678-1234-5678-1234-567812345678")
        cover = new_cover("orders", root, edition="branch-a")
        assert cover.edition == "branch-a"

    def test_new_command_page(self) -> None:
        """new_command_page carries the command in the Command arm."""
        any_msg = ProtoAny(type_url="test/Cmd", value=b"data")
        page = new_command_page(any_msg)
        assert page.WhichOneof("body") == "command"
        assert page.command.payload == any_msg

    def test_new_command_book(self) -> None:
        """new_command_book addresses its single page to the cover."""
        root = PyUUID("12345678-1234-5678-1234-567812345678")
        cover = new_cover("orders", root)
        any_msg = ProtoAny(type_url="test/Cmd", value=b"data")

        book = new_command_book(cover, new_command_page(any_msg))
        assert book.cover.domain == "orders"
        assert book.page.command.payload == any_msg

    def test_range_selection_lower_only(self) -> None:
        """range_selection with lower bound only."""
        r = range_selection(5)
        assert r.lower == 5
        assert r.upper == 0  # Default

    def test_range_selection_with_upper(self) -> None:
        """range_selection with both bounds."""
        r = range_selection(5, 10)
        assert r.lower == 5
        assert r.upper == 10

    def test_temporal_by_sequence(self) -> None:
        """temporal_by_sequence creates as-of query."""
        tq = temporal_by_sequence(42)
        assert tq.as_of_sequence == 42

    def test_temporal_by_time(self) -> None:
        """temporal_by_time creates time-based query."""
        ts = parse_timestamp("2024-01-15T10:30:00Z")
        tq = temporal_by_time(ts)
        assert tq.as_of_time.seconds == ts.seconds


class TestAdditionalHelpers:
    """Targeted tests to fill remaining coverage gaps."""

    def test_bytes_to_uuid_text_16_bytes(self) -> None:
        from uuid import UUID as PyUUID

        from angzarr_client.helpers import bytes_to_uuid_text

        u = PyUUID("12345678-1234-5678-1234-567812345678")
        assert bytes_to_uuid_text(u.bytes) == str(u)

    def test_bytes_to_uuid_text_non_16_bytes(self) -> None:
        from angzarr_client.helpers import bytes_to_uuid_text

        assert bytes_to_uuid_text(b"\x01\x02") == "0102"

    def test_proto_uuid_to_text_none(self) -> None:
        from angzarr_client.helpers import proto_uuid_to_text

        assert proto_uuid_to_text(None) == ""

    def test_proto_uuid_to_text_value(self) -> None:
        from uuid import UUID as PyUUID

        from angzarr_client.helpers import proto_uuid_to_text, uuid_to_proto

        u = PyUUID("12345678-1234-5678-1234-567812345678")
        assert proto_uuid_to_text(uuid_to_proto(u)) == str(u)

    def test_proto_uuid_to_hex_none(self) -> None:
        from angzarr_client.helpers import proto_uuid_to_hex

        assert proto_uuid_to_hex(None) == ""

    def test_proto_uuid_to_hex_value(self) -> None:
        from uuid import UUID as PyUUID

        from angzarr_client.helpers import proto_uuid_to_hex, uuid_to_proto

        u = PyUUID("12345678-1234-5678-1234-567812345678")
        assert proto_uuid_to_hex(uuid_to_proto(u)) == u.bytes.hex()

    # ``root_id_text(cover)`` accessor moved to ``Cover.root_id_hex()``;
    # for the standard 8-4-4-4-12 text form callers can use
    # ``bytes_to_uuid_text(cover.proto().root.value)``.

    # Audit #86: edition_is_empty + edition_name_or_default deleted as
    # dead-on-arrival helpers; their tests removed in lockstep.

    def test_type_matches_none_returns_false(self) -> None:
        from angzarr_client._pb import Cover
        from angzarr_client.helpers import type_matches

        assert type_matches(None, Cover) is False

    def test_type_matches_true(self) -> None:
        from google.protobuf.any_pb2 import Any

        from angzarr_client._pb import Cover
        from angzarr_client.helpers import type_matches

        any_proto = Any()
        any_proto.Pack(Cover(domain="x"))
        assert type_matches(any_proto, Cover) is True

    def test_try_unpack_returns_message(self) -> None:
        from google.protobuf.any_pb2 import Any

        from angzarr_client._pb import Cover
        from angzarr_client.helpers import try_unpack

        any_proto = Any()
        any_proto.Pack(Cover(domain="x"))
        msg = try_unpack(any_proto, Cover)
        assert msg is not None and msg.domain == "x"

    def test_try_unpack_returns_none_for_mismatch(self) -> None:
        from google.protobuf.any_pb2 import Any

        from angzarr_client._pb import Cover, EventBook
        from angzarr_client.helpers import try_unpack

        any_proto = Any()
        any_proto.Pack(EventBook())
        assert try_unpack(any_proto, Cover) is None

    def test_unpack_raises_on_mismatch(self) -> None:
        import pytest
        from google.protobuf.any_pb2 import Any

        from angzarr_client._pb import Cover, EventBook
        from angzarr_client.helpers import unpack

        any_proto = Any()
        any_proto.Pack(EventBook())
        with pytest.raises(ValueError, match="type mismatch"):
            unpack(any_proto, Cover)

    def test_unpack_mismatch_names_expected_and_actual(self) -> None:
        from google.protobuf.any_pb2 import Any

        from angzarr_client._pb import Cover, EventBook
        from angzarr_client.helpers import unpack

        any_proto = Any()
        any_proto.Pack(EventBook())
        with pytest.raises(ValueError) as exc:
            unpack(any_proto, Cover)
        assert str(exc.value) == (
            f"type mismatch: expected {Cover.DESCRIPTOR.full_name}, "
            f"got {any_proto.type_url}"
        )

    def test_unpack_returns_message(self) -> None:
        from google.protobuf.any_pb2 import Any

        from angzarr_client._pb import Cover
        from angzarr_client.helpers import unpack

        any_proto = Any()
        any_proto.Pack(Cover(domain="xyz"))
        msg = unpack(any_proto, Cover)
        assert msg.domain == "xyz"

    def test_full_type_name(self) -> None:
        from angzarr_client._pb import Cover
        from angzarr_client.helpers import full_type_name

        assert full_type_name(Cover) == "io.angzarr.v1.Cover"

    def test_full_type_url_for_and_alias(self) -> None:
        from angzarr_client._pb import Cover
        from angzarr_client.helpers import (
            full_type_url,
            full_type_url_for,
        )

        assert full_type_url_for(Cover) == "/io.angzarr.v1.Cover"
        assert full_type_url is full_type_url_for

    def test_decode_event_returns_none_on_unpack_failure(self, monkeypatch) -> None:
        from angzarr_client._pb import Cover, EventPage
        from angzarr_client.helpers import decode_event

        page = EventPage(sequence=1)
        page.payload.Pack(Cover(domain="x"))

        def boom(self, _msg):
            raise RuntimeError("decoding failed")

        # Patch Unpack on the Any to force the except branch
        monkeypatch.setattr(type(page.payload), "Unpack", boom)
        assert decode_event(page, "io.angzarr.v1.Cover", Cover) is None


# Audit #86 reverted 2026-04-29: edition propagation moved to
# coordinator-contract; the four `TestPropagateEditionFrom` tests
# were deleted along with the `propagate_edition_from` helper.


class TestCorrelatedMetadata:
    """Audit #69: ``correlated_metadata`` mirrors Rust's
    ``proto_ext::correlated_request`` — same wire surface
    (``x-correlation-id`` header), per-language idiomatic call shape."""

    def test_returns_metadata_pair_for_non_empty_id(self) -> None:
        from angzarr_client.helpers import (
            CORRELATION_ID_HEADER,
            correlated_metadata,
        )

        md = correlated_metadata("abc-123")
        assert md == [(CORRELATION_ID_HEADER, "abc-123")]

    def test_returns_empty_list_for_empty_id(self) -> None:
        from angzarr_client.helpers import correlated_metadata

        assert correlated_metadata("") == []

    def test_uuid_format_id_passes_through(self) -> None:
        from angzarr_client.helpers import (
            CORRELATION_ID_HEADER,
            correlated_metadata,
        )

        uuid_id = "550e8400-e29b-41d4-a716-446655440000"
        md = correlated_metadata(uuid_id)
        assert md == [(CORRELATION_ID_HEADER, uuid_id)]

    def test_header_value_is_x_correlation_id(self) -> None:
        # Cross-language wire contract — Rust and every grpc-py caller
        # must agree on this exact header name.
        from angzarr_client.helpers import CORRELATION_ID_HEADER

        assert CORRELATION_ID_HEADER == "x-correlation-id"

    def test_alphanumeric_dash_underscore_id_passes(self) -> None:
        # Mirrors Rust's `test_correlated_request_with_common_id_formats`.
        from angzarr_client.helpers import (
            CORRELATION_ID_HEADER,
            correlated_metadata,
        )

        for sample in ["abc123", "req-42", "trace_001", "corr.7"]:
            md = correlated_metadata(sample)
            assert md == [(CORRELATION_ID_HEADER, sample)], sample

    def test_returns_list_not_tuple(self) -> None:
        # grpc-py expects metadata as a list/iterable of 2-tuples; pin
        # the outer container shape to avoid silently passing the wrong
        # type to a stub call.
        from angzarr_client.helpers import correlated_metadata

        assert isinstance(correlated_metadata("x"), list)
        assert isinstance(correlated_metadata("x")[0], tuple)
        assert len(correlated_metadata("x")[0]) == 2

    def test_proto_uuid_renders_its_text_form(self) -> None:
        # Correlation travels as a proto UUID (CommandPage.correlation_id,
        # Query.correlation_id); the header carries its standard text form.
        from angzarr_client.helpers import correlated_metadata

        u = PyUUID("0192f0c1-0000-7000-8000-00000000c0de")
        assert correlated_metadata(uuid_to_proto(u)) == [
            (CORRELATION_ID_HEADER, str(u))
        ]

    def test_unset_proto_uuid_skips_the_header(self) -> None:
        from angzarr_client._pb import UUID
        from angzarr_client.helpers import correlated_metadata

        assert correlated_metadata(UUID()) == []
        assert correlated_metadata(None) == []
