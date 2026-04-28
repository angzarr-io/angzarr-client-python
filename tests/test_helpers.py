"""Tests for helper functions."""

from datetime import datetime, timezone
from uuid import UUID as PyUUID

import pytest
from google.protobuf.any_pb2 import Any as ProtoAny

from angzarr_client.errors import InvalidTimestampError
from angzarr_client.helpers import (
    CORRELATION_ID_HEADER,
    DEFAULT_EDITION,
    META_ANGZARR_DOMAIN,
    PROJECTION_DOMAIN_PREFIX,
    TYPE_URL_PREFIX,
    # Constants
    UNKNOWN_DOMAIN,
    WILDCARD_DOMAIN,
    cache_key,
    # CommandBook helpers
    command_pages,
    correlation_id,
    # Cover functions
    cover_of,
    # Event decoding
    decode_event,
    # Saga helpers
    destination_map,
    divergence_for,
    domain,
    edition,
    event_pages,
    # CommandResponse helpers
    events_from_response,
    explicit_edition,
    has_correlation_id,
    implicit_edition,
    is_main_timeline,
    # Edition helpers
    main_timeline,
    new_command_book,
    new_command_page,
    # Construction helpers
    new_cover,
    # EventBook helpers
    next_sequence,
    # Timestamp helpers
    now,
    parse_timestamp,
    proto_to_uuid,
    range_selection,
    root_id_hex,
    root_uuid,
    routing_key,
    temporal_by_sequence,
    temporal_by_time,
    type_name_from_url,
    # Type URL helpers
    type_url,
    type_url_matches,
    # UUID conversion
    uuid_to_proto,
)
from angzarr_client.proto.angzarr import (
    UUID,
    CommandBook,
    CommandPage,
    Cover,
    DomainDivergence,
    Edition,
    EventBook,
    EventPage,
    PageHeader,
    Query,
)


class TestConstants:
    """Tests for module constants."""

    def test_unknown_domain(self) -> None:
        assert UNKNOWN_DOMAIN == "unknown"

    def test_wildcard_domain(self) -> None:
        assert WILDCARD_DOMAIN == "*"

    def test_default_edition(self) -> None:
        assert DEFAULT_EDITION == ""

    def test_meta_domain(self) -> None:
        assert META_ANGZARR_DOMAIN == "_angzarr"

    def test_projection_prefix(self) -> None:
        assert PROJECTION_DOMAIN_PREFIX == "_projection"

    def test_correlation_header(self) -> None:
        assert CORRELATION_ID_HEADER == "x-correlation-id"

    def test_type_url_prefix(self) -> None:
        assert TYPE_URL_PREFIX == "type.googleapis.com/"


class TestCoverOf:
    """Tests for cover_of function."""

    def test_cover_returns_self(self) -> None:
        """Cover object returns itself."""
        cover = Cover(domain="test")
        assert cover_of(cover) is cover

    def test_event_book_returns_cover(self) -> None:
        """EventBook returns its cover."""
        cover = Cover(domain="orders")
        book = EventBook()
        book.cover.CopyFrom(cover)
        result = cover_of(book)
        assert result.domain == "orders"

    def test_command_book_returns_cover(self) -> None:
        """CommandBook returns its cover."""
        cover = Cover(domain="inventory")
        book = CommandBook()
        book.cover.CopyFrom(cover)
        result = cover_of(book)
        assert result.domain == "inventory"

    def test_query_returns_cover(self) -> None:
        """Query returns its cover."""
        cover = Cover(domain="shipping")
        query = Query()
        query.cover.CopyFrom(cover)
        result = cover_of(query)
        assert result.domain == "shipping"

    def test_object_without_cover_returns_none(self) -> None:
        """Object without cover attribute returns None."""
        result = cover_of("not a cover bearer")  # type: ignore
        assert result is None


class TestDomain:
    """Tests for domain function."""

    def test_returns_domain_from_cover(self) -> None:
        """Returns domain from Cover."""
        cover = Cover(domain="payments")
        assert domain(cover) == "payments"

    def test_returns_unknown_for_empty_domain(self) -> None:
        """Returns UNKNOWN_DOMAIN for empty domain."""
        cover = Cover()
        assert domain(cover) == UNKNOWN_DOMAIN

    def test_returns_unknown_for_none(self) -> None:
        """Returns UNKNOWN_DOMAIN for invalid input."""
        assert domain("invalid") == UNKNOWN_DOMAIN  # type: ignore


class TestCorrelationId:
    """Tests for correlation_id function."""

    def test_returns_correlation_id(self) -> None:
        """Returns correlation_id from Cover."""
        cover = Cover(correlation_id="abc-123")
        assert correlation_id(cover) == "abc-123"

    def test_returns_empty_for_no_correlation(self) -> None:
        """Returns empty string if not set."""
        cover = Cover(domain="test")
        assert correlation_id(cover) == ""

    def test_returns_empty_for_invalid_input(self) -> None:
        """Returns empty string for invalid input."""
        assert correlation_id("invalid") == ""  # type: ignore


class TestHasCorrelationId:
    """Tests for has_correlation_id function."""

    def test_true_when_set(self) -> None:
        """Returns True when correlation_id is set."""
        cover = Cover(correlation_id="xyz")
        assert has_correlation_id(cover) is True

    def test_false_when_empty(self) -> None:
        """Returns False when correlation_id is empty."""
        cover = Cover()
        assert has_correlation_id(cover) is False

    def test_false_for_invalid(self) -> None:
        """Returns False for invalid input."""
        assert has_correlation_id("invalid") is False  # type: ignore


class TestRootUuid:
    """Tests for root_uuid function."""

    def test_returns_uuid(self) -> None:
        """Returns Python UUID from Cover."""
        test_uuid = PyUUID("12345678-1234-5678-1234-567812345678")
        cover = Cover(domain="test")
        cover.root.CopyFrom(uuid_to_proto(test_uuid))
        result = root_uuid(cover)
        assert result == test_uuid

    def test_returns_none_when_no_root(self) -> None:
        """Returns None when root not set."""
        cover = Cover(domain="test")
        assert root_uuid(cover) is None

    def test_returns_none_for_invalid_bytes(self) -> None:
        """Returns None for invalid UUID bytes."""
        cover = Cover(domain="test")
        cover.root.value = b"invalid"  # Not 16 bytes
        assert root_uuid(cover) is None


class TestRootIdHex:
    """Tests for root_id_hex function."""

    def test_returns_hex_string(self) -> None:
        """Returns hex representation of root UUID."""
        test_uuid = PyUUID("12345678-1234-5678-1234-567812345678")
        cover = Cover(domain="test")
        cover.root.CopyFrom(uuid_to_proto(test_uuid))
        result = root_id_hex(cover)
        assert result == test_uuid.bytes.hex()

    def test_returns_empty_when_no_root(self) -> None:
        """Returns empty string when root not set."""
        cover = Cover(domain="test")
        assert root_id_hex(cover) == ""


class TestEdition:
    """Tests for edition function."""

    def test_returns_edition_name(self) -> None:
        """Returns edition name from Cover."""
        cover = Cover(domain="test")
        cover.edition.name = "v2"
        assert edition(cover) == "v2"

    def test_returns_none_when_not_set(self) -> None:
        """Returns None when not set."""
        cover = Cover(domain="test")
        assert edition(cover) is None

    def test_returns_none_for_empty_name(self) -> None:
        """Returns None for empty name."""
        cover = Cover(domain="test")
        cover.edition.name = ""
        assert edition(cover) is None


class TestRoutingKey:
    """Tests for routing_key function."""

    def test_returns_domain(self) -> None:
        """Routing key is the domain."""
        cover = Cover(domain="orders")
        assert routing_key(cover) == "orders"


class TestCacheKey:
    """Tests for cache_key function."""

    def test_returns_domain_and_root(self) -> None:
        """Cache key combines edition, domain and root hex."""
        test_uuid = PyUUID("12345678-1234-5678-1234-567812345678")
        cover = Cover(domain="orders")
        cover.root.CopyFrom(uuid_to_proto(test_uuid))
        result = cache_key(cover)
        # No edition set → empty prefix
        assert result == f":orders:{test_uuid.bytes.hex()}"

    def test_returns_domain_with_empty_root(self) -> None:
        """Cache key with no root has empty suffix."""
        cover = Cover(domain="orders")
        # No edition set → empty prefix
        assert cache_key(cover) == ":orders:"


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


class TestEditionHelpers:
    """Tests for edition helper functions."""

    def test_main_timeline(self) -> None:
        """main_timeline returns default edition."""
        ed = main_timeline()
        assert ed.name == DEFAULT_EDITION
        assert len(ed.divergences) == 0

    def test_implicit_edition(self) -> None:
        """implicit_edition creates named edition without divergences."""
        ed = implicit_edition("branch-a")
        assert ed.name == "branch-a"
        assert len(ed.divergences) == 0

    def test_explicit_edition(self) -> None:
        """explicit_edition creates edition with divergences."""
        divergences = [
            DomainDivergence(domain="orders", sequence=5),
            DomainDivergence(domain="inventory", sequence=10),
        ]
        ed = explicit_edition("branch-b", divergences)
        assert ed.name == "branch-b"
        assert len(ed.divergences) == 2

    def test_is_main_timeline_none(self) -> None:
        """is_main_timeline returns True for None."""
        assert is_main_timeline(None) is True

    def test_is_main_timeline_empty_name(self) -> None:
        """is_main_timeline returns True for empty name."""
        ed = Edition()
        assert is_main_timeline(ed) is True

    def test_is_main_timeline_default(self) -> None:
        """is_main_timeline returns True for default edition."""
        ed = Edition(name=DEFAULT_EDITION)
        assert is_main_timeline(ed) is True

    def test_is_main_timeline_other(self) -> None:
        """is_main_timeline returns False for other editions."""
        ed = Edition(name="speculative")
        assert is_main_timeline(ed) is False

    def test_divergence_for_found(self) -> None:
        """divergence_for returns sequence when found."""
        ed = Edition(
            name="test",
            divergences=[DomainDivergence(domain="orders", sequence=42)],
        )
        assert divergence_for(ed, "orders") == 42

    def test_divergence_for_not_found(self) -> None:
        """divergence_for returns ``None`` when not found.

        Audit #49: shifted from -1 sentinel to ``Optional[int]`` to match
        Rust's ``Option<u32>`` and stop conflating "missing" with the
        impossible "negative-sequence" case.
        """
        ed = Edition(
            name="test",
            divergences=[DomainDivergence(domain="orders", sequence=42)],
        )
        assert divergence_for(ed, "inventory") is None

    def test_divergence_for_none(self) -> None:
        """divergence_for returns ``None`` for a ``None`` edition."""
        assert divergence_for(None, "orders") is None


class TestEventBookHelpers:
    """Tests for EventBook helper functions."""

    def test_next_sequence_returns_value(self) -> None:
        """next_sequence returns the next_sequence field."""
        book = EventBook()
        book.next_sequence = 5
        assert next_sequence(book) == 5

    def test_next_sequence_none_returns_zero(self) -> None:
        """next_sequence returns 0 for None."""
        assert next_sequence(None) == 0  # type: ignore

    def test_event_pages_returns_list(self) -> None:
        """event_pages returns pages as list."""
        book = EventBook()
        page1 = EventPage(header=PageHeader(sequence=1))
        page2 = EventPage(header=PageHeader(sequence=2))
        book.pages.extend([page1, page2])
        result = event_pages(book)
        assert len(result) == 2
        assert result[0].header.sequence == 1
        assert result[1].header.sequence == 2

    def test_event_pages_none_returns_empty(self) -> None:
        """event_pages returns empty list for None."""
        assert event_pages(None) == []


class TestDestinationMap:
    """Tests for destination_map function."""

    def test_builds_map_from_destinations(self) -> None:
        """destination_map builds hex-keyed map from EventBook list."""
        uuid1 = PyUUID("11111111-1111-1111-1111-111111111111")
        uuid2 = PyUUID("22222222-2222-2222-2222-222222222222")

        book1 = EventBook(next_sequence=5)
        book1.cover.domain = "player"
        book1.cover.root.CopyFrom(uuid_to_proto(uuid1))

        book2 = EventBook(next_sequence=10)
        book2.cover.domain = "player"
        book2.cover.root.CopyFrom(uuid_to_proto(uuid2))

        result = destination_map([book1, book2])

        assert len(result) == 2
        assert result[uuid1.bytes.hex()] is book1
        assert result[uuid2.bytes.hex()] is book2

    def test_empty_list_returns_empty_map(self) -> None:
        """destination_map returns empty dict for empty list."""
        assert destination_map([]) == {}

    def test_skips_entries_without_root(self) -> None:
        """destination_map skips EventBooks without root set."""
        uuid1 = PyUUID("11111111-1111-1111-1111-111111111111")

        book_with_root = EventBook(next_sequence=5)
        book_with_root.cover.domain = "player"
        book_with_root.cover.root.CopyFrom(uuid_to_proto(uuid1))

        book_without_root = EventBook(next_sequence=10)
        book_without_root.cover.domain = "player"
        # No root set

        result = destination_map([book_with_root, book_without_root])

        assert len(result) == 1
        assert uuid1.bytes.hex() in result

    def test_works_with_next_sequence_lookup(self) -> None:
        """destination_map integrates with next_sequence for lookups."""
        uuid1 = PyUUID("11111111-1111-1111-1111-111111111111")

        book = EventBook(next_sequence=42)
        book.cover.domain = "player"
        book.cover.root.CopyFrom(uuid_to_proto(uuid1))

        dest_map = destination_map([book])
        key = uuid1.bytes.hex()

        # Pattern used in sagas: next_sequence(dest_map.get(key))
        assert next_sequence(dest_map.get(key)) == 42
        assert next_sequence(dest_map.get("nonexistent")) == 0


class TestCommandBookHelpers:
    """Tests for CommandBook helper functions."""

    def test_command_pages_returns_list(self) -> None:
        """command_pages returns pages as list."""
        book = CommandBook()
        page1 = CommandPage()
        page1.header.sequence = 1
        page2 = CommandPage()
        page2.header.sequence = 2
        book.pages.extend([page1, page2])
        result = command_pages(book)
        assert len(result) == 2
        assert result[0].header.sequence == 1

    def test_command_pages_none_returns_empty(self) -> None:
        """command_pages returns empty list for None."""
        assert command_pages(None) == []


class TestEventsFromResponse:
    """Tests for events_from_response function."""

    def test_returns_none_for_none_response(self) -> None:
        """Returns empty list for None response."""
        assert events_from_response(None) == []

    def test_returns_empty_for_no_events_field(self) -> None:
        """Returns empty list when events field not set."""
        from angzarr_client.proto.angzarr import CommandResponse

        resp = CommandResponse()
        assert events_from_response(resp) == []

    def test_returns_pages_when_present(self) -> None:
        """Returns event pages when present."""
        from angzarr_client.proto.angzarr import CommandResponse

        resp = CommandResponse()
        page1 = resp.events.pages.add()
        page1.header.sequence = 1
        page2 = resp.events.pages.add()
        page2.header.sequence = 2
        result = events_from_response(resp)
        assert len(result) == 2


class TestTypeUrlHelpers:
    """Tests for type URL helper functions."""

    def test_type_url_construction(self) -> None:
        """type_url constructs full URL from a fully-qualified type name.

        Audit finding #32 (Option A): single-arg signature matching Rust."""
        result = type_url("com.example.MyMessage")
        assert result == "type.googleapis.com/com.example.MyMessage"

    def test_type_url_preserves_angzarr_proto_prefix(self) -> None:
        """Python's actual proto packages are ``angzarr_client.proto.*``,
        so Python wire URLs include that prefix verbatim. ``wire_name``
        is a no-op in Python; ``type_url`` echoes the input unchanged
        through the prefix."""
        result = type_url("angzarr_client.proto.examples.OrderCreated")
        assert (
            result == "type.googleapis.com/angzarr_client.proto.examples.OrderCreated"
        )

    def test_wire_name_is_identity_in_python(self) -> None:
        """``wire_name`` is identity in Python — Python's protoc emits
        the full proto package on the wire, so there's no prefix to
        strip. The function exists for cross-language API symmetry
        with Rust's ``convert::wire_name``."""
        from angzarr_client.helpers import wire_name

        assert wire_name("examples.OrderCreated") == "examples.OrderCreated"
        assert (
            wire_name("angzarr_client.proto.examples.OrderCreated")
            == "angzarr_client.proto.examples.OrderCreated"
        )

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

    def test_type_url_matches_with_full_python_proto_prefix(self) -> None:
        """Python wire URLs include the ``angzarr_client.proto.*`` prefix
        because Python protoc honors the proto package declaration.
        ``type_url_matches`` accepts the corresponding ``type_name`` form."""
        assert (
            type_url_matches(
                "type.googleapis.com/angzarr_client.proto.examples.OrderCreated",
                "angzarr_client.proto.examples.OrderCreated",
            )
            is True
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


class TestDecodeEvent:
    """Tests for decode_event function."""

    def test_returns_none_for_none_page(self) -> None:
        """Returns None for None page."""
        from angzarr_client.proto.angzarr import Cover

        assert decode_event(None, "Cover", Cover) is None

    def test_returns_none_for_no_event_field(self) -> None:
        """Returns None when event field not set."""
        from angzarr_client.proto.angzarr import Cover

        page = EventPage(header=PageHeader(sequence=1))
        assert decode_event(page, "Cover", Cover) is None

    def test_returns_none_for_type_mismatch(self) -> None:
        """Returns None when type URL doesn't match."""
        from angzarr_client.proto.angzarr import Cover

        page = EventPage(header=PageHeader(sequence=1))
        page.event.type_url = "type.googleapis.com/some.OtherType"
        page.event.value = b""
        assert decode_event(page, "Cover", Cover) is None

    def test_returns_decoded_message(self) -> None:
        """Returns decoded message when type matches."""
        from angzarr_client.proto.angzarr import Cover

        # Create a cover and pack it
        cover = Cover(domain="test", correlation_id="abc")
        page = EventPage(header=PageHeader(sequence=1))
        page.event.Pack(cover)

        # Use full type name for exact matching
        result = decode_event(page, "angzarr_client.proto.angzarr.Cover", Cover)
        assert result is not None
        assert result.domain == "test"
        assert result.correlation_id == "abc"

    def test_returns_none_for_decode_failure(self) -> None:
        """Returns None when decoding fails."""
        from angzarr_client.proto.angzarr import Cover

        # Create page with matching type URL but invalid data
        page = EventPage(header=PageHeader(sequence=1))
        page.event.type_url = "type.googleapis.com/angzarr.Cover"
        page.event.value = b"invalid proto data that will fail to decode"
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
        assert cover.correlation_id == ""

    def test_new_cover_with_correlation(self) -> None:
        """new_cover accepts correlation_id."""
        root = PyUUID("12345678-1234-5678-1234-567812345678")
        cover = new_cover("orders", root, correlation_id_val="corr-123")
        assert cover.correlation_id == "corr-123"

    def test_new_cover_with_edition(self) -> None:
        """new_cover accepts edition."""
        root = PyUUID("12345678-1234-5678-1234-567812345678")
        ed = implicit_edition("branch-a")
        cover = new_cover("orders", root, edition_val=ed)
        assert cover.edition.name == "branch-a"

    def test_new_command_page(self) -> None:
        """new_command_page creates page with sequence and command."""
        any_msg = ProtoAny(type_url="test/Cmd", value=b"data")
        page = new_command_page(5, any_msg)
        assert page.header.sequence == 5
        assert page.command.type_url == "test/Cmd"

    def test_new_command_book(self) -> None:
        """new_command_book creates book with cover and pages."""
        root = PyUUID("12345678-1234-5678-1234-567812345678")
        cover = new_cover("orders", root)
        any_msg = ProtoAny(type_url="test/Cmd", value=b"data")
        pages = [new_command_page(0, any_msg)]

        book = new_command_book(cover, pages)
        assert book.cover.domain == "orders"
        assert len(book.pages) == 1
        assert book.pages[0].header.sequence == 0

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

    def test_root_id_text_with_root(self) -> None:
        from uuid import UUID as PyUUID

        from angzarr_client.helpers import root_id_text, uuid_to_proto
        from angzarr_client.proto.angzarr import Cover

        u = PyUUID("12345678-1234-5678-1234-567812345678")
        c = Cover()
        c.root.CopyFrom(uuid_to_proto(u))
        assert root_id_text(c) == str(u)

    def test_root_id_text_empty_without_root(self) -> None:
        from angzarr_client.helpers import root_id_text
        from angzarr_client.proto.angzarr import Cover

        assert root_id_text(Cover()) == ""

    def test_edition_is_empty(self) -> None:
        from angzarr_client.helpers import edition_is_empty
        from angzarr_client.proto.angzarr import Edition

        assert edition_is_empty(None) is True
        assert edition_is_empty(Edition()) is True
        assert edition_is_empty(Edition(name="v1")) is False

    def test_edition_name_or_default(self) -> None:
        from angzarr_client.helpers import (
            DEFAULT_EDITION,
            edition_name_or_default,
        )
        from angzarr_client.proto.angzarr import Edition

        assert edition_name_or_default(None) == DEFAULT_EDITION
        assert edition_name_or_default(Edition()) == DEFAULT_EDITION
        assert edition_name_or_default(Edition(name="speculative")) == "speculative"

    def test_type_matches_none_returns_false(self) -> None:
        from angzarr_client.helpers import type_matches
        from angzarr_client.proto.angzarr import Cover

        assert type_matches(None, Cover) is False

    def test_type_matches_true(self) -> None:
        from google.protobuf.any_pb2 import Any

        from angzarr_client.helpers import type_matches
        from angzarr_client.proto.angzarr import Cover

        any_proto = Any()
        any_proto.Pack(Cover(domain="x"))
        assert type_matches(any_proto, Cover) is True

    def test_try_unpack_returns_message(self) -> None:
        from google.protobuf.any_pb2 import Any

        from angzarr_client.helpers import try_unpack
        from angzarr_client.proto.angzarr import Cover

        any_proto = Any()
        any_proto.Pack(Cover(domain="x"))
        msg = try_unpack(any_proto, Cover)
        assert msg is not None and msg.domain == "x"

    def test_try_unpack_returns_none_for_mismatch(self) -> None:
        from google.protobuf.any_pb2 import Any

        from angzarr_client.helpers import try_unpack
        from angzarr_client.proto.angzarr import Cover, EventBook

        any_proto = Any()
        any_proto.Pack(EventBook())
        assert try_unpack(any_proto, Cover) is None

    def test_unpack_raises_on_mismatch(self) -> None:
        import pytest
        from google.protobuf.any_pb2 import Any

        from angzarr_client.helpers import unpack
        from angzarr_client.proto.angzarr import Cover, EventBook

        any_proto = Any()
        any_proto.Pack(EventBook())
        with pytest.raises(ValueError, match="type mismatch"):
            unpack(any_proto, Cover)

    def test_unpack_returns_message(self) -> None:
        from google.protobuf.any_pb2 import Any

        from angzarr_client.helpers import unpack
        from angzarr_client.proto.angzarr import Cover

        any_proto = Any()
        any_proto.Pack(Cover(domain="xyz"))
        msg = unpack(any_proto, Cover)
        assert msg.domain == "xyz"

    def test_full_type_name(self) -> None:
        from angzarr_client.helpers import full_type_name
        from angzarr_client.proto.angzarr import Cover

        assert full_type_name(Cover) == "angzarr_client.proto.angzarr.Cover"

    def test_full_type_url_for_and_alias(self) -> None:
        from angzarr_client.helpers import (
            TYPE_URL_PREFIX,
            full_type_url,
            full_type_url_for,
        )
        from angzarr_client.proto.angzarr import Cover

        assert (
            full_type_url_for(Cover)
            == f"{TYPE_URL_PREFIX}angzarr_client.proto.angzarr.Cover"
        )
        assert full_type_url is full_type_url_for

    def test_decode_event_returns_none_on_unpack_failure(self, monkeypatch) -> None:
        from angzarr_client.helpers import decode_event
        from angzarr_client.proto.angzarr import Cover, EventPage, PageHeader

        page = EventPage(header=PageHeader(sequence=1))
        page.event.Pack(Cover(domain="x"))

        def boom(self, _msg):
            raise RuntimeError("decoding failed")

        # Patch Unpack on the Any to force the except branch
        monkeypatch.setattr(type(page.event), "Unpack", boom)
        assert decode_event(page, "angzarr_client.proto.angzarr.Cover", Cover) is None


class TestIdempotencyKey:
    """Tests for ``idempotency_key`` — composite saga-deferred dedup key.

    Audit finding #55: ported from Rust's
    ``AngzarrDeferredSequenceExt::idempotency_key``.
    """

    def test_returns_composite_key_with_all_fields(self) -> None:
        from angzarr_client.helpers import idempotency_key
        from angzarr_client.proto.angzarr.types_pb2 import (
            AngzarrDeferredSequence,
        )

        cover = Cover(
            domain="orders",
            root=UUID(value=b"\x01" * 16),
            edition=Edition(name="angzarr"),
        )
        deferred = AngzarrDeferredSequence(source=cover, source_seq=7)

        # Format: {edition}:{domain}:{root_hex}:{source_seq}
        assert idempotency_key(deferred) == f"angzarr:orders:{'01' * 16}:7"

    def test_returns_none_when_source_missing(self) -> None:
        from angzarr_client.helpers import idempotency_key
        from angzarr_client.proto.angzarr.types_pb2 import (
            AngzarrDeferredSequence,
        )

        # No source set — should return None, not raise.
        deferred = AngzarrDeferredSequence(source_seq=3)
        assert idempotency_key(deferred) is None

    def test_returns_none_when_input_is_none(self) -> None:
        from angzarr_client.helpers import idempotency_key

        assert idempotency_key(None) is None

    def test_empty_edition_name_yields_empty_first_part(self) -> None:
        from angzarr_client.helpers import idempotency_key
        from angzarr_client.proto.angzarr.types_pb2 import (
            AngzarrDeferredSequence,
        )

        # No explicit edition — first segment is empty (default-edition convention).
        cover = Cover(domain="players", root=UUID(value=b"\xab" * 16))
        deferred = AngzarrDeferredSequence(source=cover, source_seq=42)
        assert idempotency_key(deferred) == f":players:{'ab' * 16}:42"

    def test_empty_root_yields_empty_root_segment(self) -> None:
        from angzarr_client.helpers import idempotency_key
        from angzarr_client.proto.angzarr.types_pb2 import (
            AngzarrDeferredSequence,
        )

        cover = Cover(domain="orders", edition=Edition(name="angzarr"))
        deferred = AngzarrDeferredSequence(source=cover, source_seq=1)
        assert idempotency_key(deferred) == "angzarr:orders::1"


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
