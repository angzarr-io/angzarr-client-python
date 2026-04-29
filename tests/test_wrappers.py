"""Tests for protobuf wrapper classes."""

from uuid import UUID as PyUUID


from angzarr_client.helpers import (
    UNKNOWN_DOMAIN,
    uuid_to_proto,
)
from angzarr_client.proto.angzarr import (
    CommandBook,
    CommandPage,
    CommandResponse,
    Cover,
    EventBook,
    EventPage,
    PageHeader,
    Query,
)
from angzarr_client.wrappers import (
    CommandBookW,
    CommandPageW,
    CommandResponseW,
    CoverW,
    EventBookW,
    EventPageW,
    QueryW,
)


class TestEventBookW:
    """Tests for EventBook wrapper class."""

    def test_constructor_accepts_proto(self) -> None:
        """Wrapper accepts EventBook proto in constructor."""
        proto = EventBook()
        wrapper = EventBookW(proto)
        assert wrapper.proto is proto

    def test_next_sequence_returns_value(self) -> None:
        """next_sequence returns the next_sequence field."""
        proto = EventBook()
        proto.next_sequence = 5
        wrapper = EventBookW(proto)
        assert wrapper.next_sequence() == 5

    def test_next_sequence_default_zero(self) -> None:
        """next_sequence returns 0 for new EventBook."""
        wrapper = EventBookW(EventBook())
        assert wrapper.next_sequence() == 0

    def test_pages_returns_wrapped_list(self) -> None:
        """pages returns event pages as wrapped EventPageW instances."""
        proto = EventBook()
        page1 = EventPage(header=PageHeader(sequence=1))
        page2 = EventPage(header=PageHeader(sequence=2))
        proto.pages.extend([page1, page2])
        wrapper = EventBookW(proto)
        result = wrapper.pages()
        assert len(result) == 2
        assert isinstance(result[0], EventPageW)
        assert isinstance(result[1], EventPageW)
        assert result[0].proto.header.sequence == 1
        assert result[1].proto.header.sequence == 2

    def test_pages_returns_empty_list_when_none(self) -> None:
        """pages returns empty list for new EventBook."""
        wrapper = EventBookW(EventBook())
        assert wrapper.pages() == []

    def test_domain_returns_domain_from_cover(self) -> None:
        """domain returns domain from embedded cover."""
        proto = EventBook()
        proto.cover.domain = "orders"
        wrapper = EventBookW(proto)
        assert wrapper.domain() == "orders"

    def test_domain_returns_unknown_when_not_set(self) -> None:
        """domain returns UNKNOWN_DOMAIN when cover not set."""
        wrapper = EventBookW(EventBook())
        assert wrapper.domain() == UNKNOWN_DOMAIN

    def test_correlation_id_returns_value(self) -> None:
        """correlation_id returns value from cover."""
        proto = EventBook()
        proto.cover.correlation_id = "corr-123"
        wrapper = EventBookW(proto)
        assert wrapper.correlation_id() == "corr-123"

    def test_correlation_id_returns_empty_when_not_set(self) -> None:
        """correlation_id returns empty string when not set."""
        wrapper = EventBookW(EventBook())
        assert wrapper.correlation_id() == ""

    def test_has_correlation_id_true(self) -> None:
        """has_correlation_id returns True when set."""
        proto = EventBook()
        proto.cover.correlation_id = "xyz"
        wrapper = EventBookW(proto)
        assert wrapper.has_correlation_id() is True

    def test_has_correlation_id_false(self) -> None:
        """has_correlation_id returns False when not set."""
        wrapper = EventBookW(EventBook())
        assert wrapper.has_correlation_id() is False

    def test_root_uuid_returns_uuid(self) -> None:
        """root_uuid returns Python UUID from cover."""
        test_uuid = PyUUID("12345678-1234-5678-1234-567812345678")
        proto = EventBook()
        proto.cover.root.CopyFrom(uuid_to_proto(test_uuid))
        wrapper = EventBookW(proto)
        assert wrapper.root_uuid() == test_uuid

    def test_root_uuid_returns_none_when_not_set(self) -> None:
        """root_uuid returns None when root not set."""
        wrapper = EventBookW(EventBook())
        assert wrapper.root_uuid() is None

    def test_edition_returns_edition_name(self) -> None:
        """edition returns edition name from cover."""
        proto = EventBook()
        proto.cover.edition.name = "v2"
        wrapper = EventBookW(proto)
        assert wrapper.edition() == "v2"

    def test_edition_returns_none_when_not_set(self) -> None:
        """edition returns None when not set."""
        wrapper = EventBookW(EventBook())
        assert wrapper.edition() is None

    def test_routing_key_returns_domain(self) -> None:
        """routing_key returns the domain."""
        proto = EventBook()
        proto.cover.domain = "inventory"
        wrapper = EventBookW(proto)
        assert wrapper.routing_key() == "inventory"

    def test_cache_key_returns_domain_and_root(self) -> None:
        """cache_key returns domain:root_hex format when no edition."""
        test_uuid = PyUUID("12345678-1234-5678-1234-567812345678")
        proto = EventBook()
        proto.cover.domain = "orders"
        proto.cover.root.CopyFrom(uuid_to_proto(test_uuid))
        wrapper = EventBookW(proto)
        assert wrapper.cache_key() == f":orders:{test_uuid.bytes.hex()}"

    def test_cover_wrapper_returns_cover_w(self) -> None:
        """cover_wrapper returns a CoverW wrapping the cover."""
        proto = EventBook()
        proto.cover.domain = "test"
        wrapper = EventBookW(proto)
        cover_w = wrapper.cover_wrapper()
        assert isinstance(cover_w, CoverW)
        assert cover_w.domain() == "test"


class TestCommandBookW:
    """Tests for CommandBook wrapper class."""

    def test_constructor_accepts_proto(self) -> None:
        """Wrapper accepts CommandBook proto in constructor."""
        proto = CommandBook()
        wrapper = CommandBookW(proto)
        assert wrapper.proto is proto

    def test_pages_returns_wrapped_list(self) -> None:
        """pages returns command pages as wrapped CommandPageW instances."""
        proto = CommandBook()
        page1 = CommandPage()
        page1.header.sequence = 1
        page2 = CommandPage()
        page2.header.sequence = 2
        proto.pages.extend([page1, page2])
        wrapper = CommandBookW(proto)
        result = wrapper.pages()
        assert len(result) == 2
        assert isinstance(result[0], CommandPageW)
        assert result[0].sequence() == 1

    def test_pages_returns_empty_list_when_none(self) -> None:
        """pages returns empty list for new CommandBook."""
        wrapper = CommandBookW(CommandBook())
        assert wrapper.pages() == []

    def test_domain_returns_domain_from_cover(self) -> None:
        """domain returns domain from embedded cover."""
        proto = CommandBook()
        proto.cover.domain = "fulfillment"
        wrapper = CommandBookW(proto)
        assert wrapper.domain() == "fulfillment"

    def test_correlation_id_returns_value(self) -> None:
        """correlation_id returns value from cover."""
        proto = CommandBook()
        proto.cover.correlation_id = "cmd-456"
        wrapper = CommandBookW(proto)
        assert wrapper.correlation_id() == "cmd-456"


class TestCoverW:
    """Tests for Cover wrapper class."""

    def test_constructor_accepts_proto(self) -> None:
        """Wrapper accepts Cover proto in constructor."""
        proto = Cover(domain="test")
        wrapper = CoverW(proto)
        assert wrapper.proto is proto

    def test_domain_returns_domain(self) -> None:
        """domain returns the domain field."""
        wrapper = CoverW(Cover(domain="orders"))
        assert wrapper.domain() == "orders"

    def test_domain_returns_unknown_for_empty(self) -> None:
        """domain returns UNKNOWN_DOMAIN for empty domain."""
        wrapper = CoverW(Cover())
        assert wrapper.domain() == UNKNOWN_DOMAIN

    def test_correlation_id_returns_value(self) -> None:
        """correlation_id returns the correlation_id field."""
        wrapper = CoverW(Cover(correlation_id="abc-123"))
        assert wrapper.correlation_id() == "abc-123"

    def test_correlation_id_returns_empty_for_unset(self) -> None:
        """correlation_id returns empty string if not set."""
        wrapper = CoverW(Cover())
        assert wrapper.correlation_id() == ""

    def test_has_correlation_id_true(self) -> None:
        """has_correlation_id returns True when set."""
        wrapper = CoverW(Cover(correlation_id="xyz"))
        assert wrapper.has_correlation_id() is True

    def test_has_correlation_id_false(self) -> None:
        """has_correlation_id returns False when empty."""
        wrapper = CoverW(Cover())
        assert wrapper.has_correlation_id() is False

    def test_root_uuid_returns_uuid(self) -> None:
        """root_uuid returns Python UUID."""
        test_uuid = PyUUID("deadbeef-dead-beef-dead-beefdeadbeef")
        proto = Cover()
        proto.root.CopyFrom(uuid_to_proto(test_uuid))
        wrapper = CoverW(proto)
        assert wrapper.root_uuid() == test_uuid

    def test_root_uuid_returns_none_when_not_set(self) -> None:
        """root_uuid returns None when root not set."""
        wrapper = CoverW(Cover())
        assert wrapper.root_uuid() is None

    def test_root_uuid_returns_none_for_invalid_bytes(self) -> None:
        """root_uuid returns None for invalid UUID bytes."""
        proto = Cover()
        proto.root.value = b"invalid"
        wrapper = CoverW(proto)
        assert wrapper.root_uuid() is None

    def test_root_id_hex_returns_hex(self) -> None:
        """root_id_hex returns hex string."""
        test_uuid = PyUUID("12345678-1234-5678-1234-567812345678")
        proto = Cover()
        proto.root.CopyFrom(uuid_to_proto(test_uuid))
        wrapper = CoverW(proto)
        assert wrapper.root_id_hex() == test_uuid.bytes.hex()

    def test_root_id_hex_returns_empty_when_not_set(self) -> None:
        """root_id_hex returns empty string when root not set."""
        wrapper = CoverW(Cover())
        assert wrapper.root_id_hex() == ""

    def test_edition_returns_name(self) -> None:
        """edition returns edition name."""
        proto = Cover()
        proto.edition.name = "speculative"
        wrapper = CoverW(proto)
        assert wrapper.edition() == "speculative"

    def test_edition_returns_none_when_not_set(self) -> None:
        """edition returns None when not set."""
        wrapper = CoverW(Cover())
        assert wrapper.edition() is None

    def test_routing_key_returns_domain(self) -> None:
        """routing_key returns the domain."""
        wrapper = CoverW(Cover(domain="payments"))
        assert wrapper.routing_key() == "payments"

    def test_cache_key_format(self) -> None:
        """cache_key returns edition:domain:root_hex format."""
        test_uuid = PyUUID("12345678-1234-5678-1234-567812345678")
        proto = Cover(domain="inventory")
        proto.root.CopyFrom(uuid_to_proto(test_uuid))
        wrapper = CoverW(proto)
        # No edition set → empty prefix
        expected = f":inventory:{test_uuid.bytes.hex()}"
        assert wrapper.cache_key() == expected


class TestQueryW:
    """Tests for Query wrapper class."""

    def test_constructor_accepts_proto(self) -> None:
        """Wrapper accepts Query proto in constructor."""
        proto = Query()
        wrapper = QueryW(proto)
        assert wrapper.proto is proto

    def test_domain_returns_domain_from_cover(self) -> None:
        """domain returns domain from embedded cover."""
        proto = Query()
        proto.cover.domain = "shipping"
        wrapper = QueryW(proto)
        assert wrapper.domain() == "shipping"

    def test_correlation_id_returns_value(self) -> None:
        """correlation_id returns value from cover."""
        proto = Query()
        proto.cover.correlation_id = "query-789"
        wrapper = QueryW(proto)
        assert wrapper.correlation_id() == "query-789"


class TestEventPageW:
    """Tests for EventPage wrapper class."""

    def test_constructor_accepts_proto(self) -> None:
        """Wrapper accepts EventPage proto in constructor."""
        proto = EventPage(header=PageHeader(sequence=5))
        wrapper = EventPageW(proto)
        assert wrapper.proto is proto

    def test_decode_event_returns_message(self) -> None:
        """decode_event returns decoded message when type matches."""
        cover = Cover(domain="test", correlation_id="abc")
        proto = EventPage(header=PageHeader(sequence=1))
        proto.event.Pack(cover)
        wrapper = EventPageW(proto)

        # Use full type name for exact matching
        result = wrapper.decode_event("angzarr_client.proto.angzarr.Cover", Cover)
        assert result is not None
        assert result.domain == "test"
        assert result.correlation_id == "abc"

    def test_decode_event_returns_none_for_mismatch(self) -> None:
        """decode_event returns None when type doesn't match."""
        cover = Cover(domain="test")
        proto = EventPage(header=PageHeader(sequence=1))
        proto.event.Pack(cover)
        wrapper = EventPageW(proto)

        result = wrapper.decode_event("OtherType", Cover)
        assert result is None

    def test_decode_event_returns_none_when_no_event(self) -> None:
        """decode_event returns None when event not set."""
        wrapper = EventPageW(EventPage(header=PageHeader(sequence=1)))
        result = wrapper.decode_event("Cover", Cover)
        assert result is None


class TestCommandPageW:
    """Tests for CommandPage wrapper class."""

    def test_constructor_accepts_proto(self) -> None:
        """Wrapper accepts CommandPage proto in constructor."""
        proto = CommandPage()
        proto.header.sequence = 10
        wrapper = CommandPageW(proto)
        assert wrapper.proto is proto

    def test_sequence_returns_value(self) -> None:
        """sequence returns the sequence field."""
        proto = CommandPage()
        proto.header.sequence = 42
        wrapper = CommandPageW(proto)
        assert wrapper.sequence() == 42


class TestCommandResponseW:
    """Tests for CommandResponse wrapper class."""

    def test_constructor_accepts_proto(self) -> None:
        """Wrapper accepts CommandResponse proto in constructor."""
        proto = CommandResponse()
        wrapper = CommandResponseW(proto)
        assert wrapper.proto is proto

    def test_events_book_returns_wrapped_event_book(self) -> None:
        """events_book returns EventBookW when present."""
        proto = CommandResponse()
        proto.events.next_sequence = 5
        page = proto.events.pages.add()
        page.header.sequence = 1
        wrapper = CommandResponseW(proto)
        book = wrapper.events_book()
        assert book is not None
        assert isinstance(book, EventBookW)
        assert book.next_sequence() == 5

    def test_events_book_returns_none_when_not_set(self) -> None:
        """events_book returns None when events not set."""
        wrapper = CommandResponseW(CommandResponse())
        assert wrapper.events_book() is None

    def test_events_returns_wrapped_pages_when_present(self) -> None:
        """events returns event pages as wrapped EventPageW instances."""
        proto = CommandResponse()
        page1 = proto.events.pages.add()
        page1.header.sequence = 1
        page2 = proto.events.pages.add()
        page2.header.sequence = 2
        wrapper = CommandResponseW(proto)
        result = wrapper.events()
        assert len(result) == 2
        assert isinstance(result[0], EventPageW)
        assert isinstance(result[1], EventPageW)

    def test_events_returns_empty_when_not_set(self) -> None:
        """events returns empty list when events not set."""
        wrapper = CommandResponseW(CommandResponse())
        assert wrapper.events() == []


class TestEventBookWAdditional:
    """Additional EventBookW coverage."""

    def test_is_empty_true_for_empty_book(self) -> None:
        assert EventBookW(EventBook()).is_empty() is True

    def test_is_empty_false_with_pages(self) -> None:
        proto = EventBook()
        proto.pages.add().header.sequence = 1
        assert EventBookW(proto).is_empty() is False

    def test_last_page_returns_last(self) -> None:
        proto = EventBook()
        proto.pages.add().header.sequence = 1
        proto.pages.add().header.sequence = 7
        page = EventBookW(proto).last_page()
        assert page is not None
        assert page.proto.header.sequence == 7

    def test_last_page_none_when_empty(self) -> None:
        assert EventBookW(EventBook()).last_page() is None

    def test_first_page_returns_first(self) -> None:
        proto = EventBook()
        proto.pages.add().header.sequence = 3
        proto.pages.add().header.sequence = 9
        page = EventBookW(proto).first_page()
        assert page is not None
        assert page.proto.header.sequence == 3

    def test_first_page_none_when_empty(self) -> None:
        assert EventBookW(EventBook()).first_page() is None

    def test_root_uuid_none_when_no_cover(self) -> None:
        assert EventBookW(EventBook()).root_uuid() is None

    def test_root_uuid_invalid_bytes(self) -> None:
        proto = EventBook()
        proto.cover.root.value = b"too-short"
        assert EventBookW(proto).root_uuid() is None

    def test_root_id_hex_from_cover(self) -> None:
        test_uuid = PyUUID("12345678-1234-5678-1234-567812345678")
        proto = EventBook()
        proto.cover.root.CopyFrom(uuid_to_proto(test_uuid))
        assert EventBookW(proto).root_id_hex() == test_uuid.bytes.hex()

    def test_root_id_hex_empty_when_no_cover(self) -> None:
        assert EventBookW(EventBook()).root_id_hex() == ""

    def test_edition_none_when_no_cover(self) -> None:
        assert EventBookW(EventBook()).edition() is None

    def test_cover_wrapper_returns_empty_when_no_cover(self) -> None:
        cw = EventBookW(EventBook()).cover_wrapper()
        assert isinstance(cw, CoverW)
        assert cw.domain() == UNKNOWN_DOMAIN


class TestCommandBookWAdditional:
    """Additional CommandBookW coverage."""

    def test_command_sequence_from_first_page(self) -> None:
        proto = CommandBook()
        proto.pages.add().header.sequence = 42
        assert CommandBookW(proto).command_sequence() == 42

    def test_command_sequence_zero_when_no_pages(self) -> None:
        assert CommandBookW(CommandBook()).command_sequence() == 0

    def test_command_sequence_zero_when_page_has_no_header(self) -> None:
        proto = CommandBook()
        proto.pages.add()  # page without header set
        assert CommandBookW(proto).command_sequence() == 0

    def test_first_command_returns_first(self) -> None:
        proto = CommandBook()
        proto.pages.add().header.sequence = 1
        assert CommandBookW(proto).first_command() is not None

    def test_first_command_none_when_empty(self) -> None:
        assert CommandBookW(CommandBook()).first_command() is None

    def test_merge_strategy_default_when_no_pages(self) -> None:
        from angzarr_client.proto.angzarr import MergeStrategy

        assert (
            CommandBookW(CommandBook()).merge_strategy()
            == MergeStrategy.MERGE_COMMUTATIVE
        )

    def test_merge_strategy_from_first_page(self) -> None:
        from angzarr_client.proto.angzarr import MergeStrategy

        proto = CommandBook()
        page = proto.pages.add()
        page.merge_strategy = MergeStrategy.MERGE_STRICT
        assert CommandBookW(proto).merge_strategy() == MergeStrategy.MERGE_STRICT

    def test_domain_unknown_when_no_cover(self) -> None:
        assert CommandBookW(CommandBook()).domain() == UNKNOWN_DOMAIN

    def test_correlation_id_empty_when_no_cover(self) -> None:
        assert CommandBookW(CommandBook()).correlation_id() == ""

    def test_has_correlation_id_true(self) -> None:
        proto = CommandBook()
        proto.cover.correlation_id = "corr"
        assert CommandBookW(proto).has_correlation_id() is True

    def test_has_correlation_id_false(self) -> None:
        assert CommandBookW(CommandBook()).has_correlation_id() is False

    def test_root_uuid_from_cover(self) -> None:
        test_uuid = PyUUID("12345678-1234-5678-1234-567812345678")
        proto = CommandBook()
        proto.cover.root.CopyFrom(uuid_to_proto(test_uuid))
        assert CommandBookW(proto).root_uuid() == test_uuid

    def test_root_uuid_none_when_no_cover(self) -> None:
        assert CommandBookW(CommandBook()).root_uuid() is None

    def test_root_uuid_none_for_invalid_bytes(self) -> None:
        proto = CommandBook()
        proto.cover.root.value = b"bad"
        assert CommandBookW(proto).root_uuid() is None

    def test_edition_from_cover(self) -> None:
        proto = CommandBook()
        proto.cover.edition.name = "v1"
        assert CommandBookW(proto).edition() == "v1"

    def test_edition_none_when_no_cover(self) -> None:
        assert CommandBookW(CommandBook()).edition() is None

    def test_routing_key_is_domain(self) -> None:
        proto = CommandBook()
        proto.cover.domain = "orders"
        assert CommandBookW(proto).routing_key() == "orders"

    def test_cache_key_with_root(self) -> None:
        # Audit #75: empty edition → empty leading segment, NOT literal "None".
        test_uuid = PyUUID("12345678-1234-5678-1234-567812345678")
        proto = CommandBook()
        proto.cover.domain = "orders"
        proto.cover.root.CopyFrom(uuid_to_proto(test_uuid))
        assert CommandBookW(proto).cache_key() == f":orders:{test_uuid.bytes.hex()}"

    def test_cache_key_without_root(self) -> None:
        proto = CommandBook()
        proto.cover.domain = "orders"
        assert CommandBookW(proto).cache_key() == ":orders:"

    def test_cache_key_matches_sibling_wrappers(self) -> None:
        # Audit #75: parity with CoverW/EventBookW formula across all book wrappers.
        from angzarr_client.wrappers import CoverW

        proto = CommandBook()
        proto.cover.domain = "orders"
        proto.cover.edition.name = "alt"
        assert CommandBookW(proto).cache_key() == CoverW(proto.cover).cache_key()

    def test_cover_wrapper(self) -> None:
        proto = CommandBook()
        proto.cover.domain = "orders"
        assert CommandBookW(proto).cover_wrapper().domain() == "orders"

    def test_cover_wrapper_empty_when_no_cover(self) -> None:
        cw = CommandBookW(CommandBook()).cover_wrapper()
        assert cw.domain() == UNKNOWN_DOMAIN


class TestQueryWAdditional:
    """Additional QueryW coverage."""

    def test_domain_unknown_when_no_cover(self) -> None:
        assert QueryW(Query()).domain() == UNKNOWN_DOMAIN

    def test_correlation_id_empty_when_no_cover(self) -> None:
        assert QueryW(Query()).correlation_id() == ""

    def test_has_correlation_id_true_and_false(self) -> None:
        assert QueryW(Query()).has_correlation_id() is False
        proto = Query()
        proto.cover.correlation_id = "x"
        assert QueryW(proto).has_correlation_id() is True

    def test_root_uuid_from_cover(self) -> None:
        test_uuid = PyUUID("12345678-1234-5678-1234-567812345678")
        proto = Query()
        proto.cover.root.CopyFrom(uuid_to_proto(test_uuid))
        assert QueryW(proto).root_uuid() == test_uuid

    def test_root_uuid_none_when_no_cover(self) -> None:
        assert QueryW(Query()).root_uuid() is None

    def test_root_uuid_none_for_invalid_bytes(self) -> None:
        proto = Query()
        proto.cover.root.value = b"bad"
        assert QueryW(proto).root_uuid() is None

    def test_routing_key_is_domain(self) -> None:
        proto = Query()
        proto.cover.domain = "inv"
        assert QueryW(proto).routing_key() == "inv"

    # Audit #81: edition / root_id_hex / cache_key parity with sibling wrappers.

    def test_edition_returns_value(self) -> None:
        proto = Query()
        proto.cover.edition.name = "alt"
        assert QueryW(proto).edition() == "alt"

    def test_edition_none_when_unset(self) -> None:
        assert QueryW(Query()).edition() is None

    def test_root_id_hex_returns_value(self) -> None:
        test_uuid = PyUUID("12345678-1234-5678-1234-567812345678")
        proto = Query()
        proto.cover.root.CopyFrom(uuid_to_proto(test_uuid))
        assert QueryW(proto).root_id_hex() == test_uuid.bytes.hex()

    def test_root_id_hex_empty_when_no_cover(self) -> None:
        assert QueryW(Query()).root_id_hex() == ""

    def test_cache_key_full_shape(self) -> None:
        test_uuid = PyUUID("12345678-1234-5678-1234-567812345678")
        proto = Query()
        proto.cover.domain = "inv"
        proto.cover.edition.name = "alt"
        proto.cover.root.CopyFrom(uuid_to_proto(test_uuid))
        assert QueryW(proto).cache_key() == f"alt:inv:{test_uuid.bytes.hex()}"

    def test_cache_key_empty_edition_no_root(self) -> None:
        proto = Query()
        proto.cover.domain = "inv"
        assert QueryW(proto).cache_key() == ":inv:"

    def test_cover_wrapper_returns_empty_when_no_cover(self) -> None:
        assert QueryW(Query()).cover_wrapper().domain() == UNKNOWN_DOMAIN

    def test_cover_wrapper_wraps_cover(self) -> None:
        proto = Query()
        proto.cover.domain = "inv"
        assert QueryW(proto).cover_wrapper().domain() == "inv"


class TestEventPageWAdditional:
    """Additional EventPageW coverage."""

    def test_sequence_num_returns_header_sequence(self) -> None:
        proto = EventPage(header=PageHeader(sequence=17))
        assert EventPageW(proto).sequence_num() == 17

    def test_sequence_num_zero_when_no_header(self) -> None:
        assert EventPageW(EventPage()).sequence_num() == 0

    def test_header_returns_header(self) -> None:
        proto = EventPage(header=PageHeader(sequence=3))
        h = EventPageW(proto).header()
        assert h is not None
        assert h.sequence == 3

    def test_header_none_when_absent(self) -> None:
        assert EventPageW(EventPage()).header() is None

    def test_is_deferred_false_when_no_header(self) -> None:
        assert EventPageW(EventPage()).is_deferred() is False

    def test_is_deferred_false_when_no_deferred_subfield(self) -> None:
        assert (
            EventPageW(EventPage(header=PageHeader(sequence=1))).is_deferred() is False
        )

    def test_is_deferred_true_for_external_deferred(self) -> None:
        proto = EventPage(header=PageHeader(sequence=1))
        proto.header.external_deferred.SetInParent()
        assert EventPageW(proto).is_deferred() is True

    def test_is_deferred_true_for_angzarr_deferred(self) -> None:
        proto = EventPage(header=PageHeader(sequence=1))
        proto.header.angzarr_deferred.SetInParent()
        assert EventPageW(proto).is_deferred() is True

    def test_type_url_returns_url(self) -> None:
        proto = EventPage(header=PageHeader(sequence=1))
        proto.event.Pack(Cover(domain="x"))
        url = EventPageW(proto).type_url()
        assert url is not None and url.endswith("angzarr_client.proto.angzarr.Cover")

    def test_type_url_none_when_no_event(self) -> None:
        assert EventPageW(EventPage(header=PageHeader(sequence=1))).type_url() is None

    def test_payload_returns_bytes(self) -> None:
        proto = EventPage(header=PageHeader(sequence=1))
        proto.event.Pack(Cover(domain="x"))
        payload = EventPageW(proto).payload()
        assert isinstance(payload, (bytes, bytearray))

    def test_payload_none_when_no_event(self) -> None:
        assert EventPageW(EventPage(header=PageHeader(sequence=1))).payload() is None

    def test_decode_typed_returns_message(self) -> None:
        proto = EventPage(header=PageHeader(sequence=1))
        proto.event.Pack(Cover(domain="zzz"))
        result = EventPageW(proto).decode_typed(Cover)
        assert result is not None and result.domain == "zzz"

    def test_decode_typed_none_when_no_event(self) -> None:
        assert (
            EventPageW(EventPage(header=PageHeader(sequence=1))).decode_typed(Cover)
            is None
        )


class TestCommandPageWAdditional:
    """Additional CommandPageW coverage."""

    def test_sequence_zero_when_no_header(self) -> None:
        assert CommandPageW(CommandPage()).sequence() == 0

    def test_header_returns_header(self) -> None:
        proto = CommandPage()
        proto.header.sequence = 5
        h = CommandPageW(proto).header()
        assert h is not None and h.sequence == 5

    def test_header_none_when_absent(self) -> None:
        assert CommandPageW(CommandPage()).header() is None

    def test_is_deferred_false_when_no_header(self) -> None:
        assert CommandPageW(CommandPage()).is_deferred() is False

    def test_is_deferred_true_for_external_deferred(self) -> None:
        proto = CommandPage()
        proto.header.sequence = 1
        proto.header.external_deferred.SetInParent()
        assert CommandPageW(proto).is_deferred() is True

    def test_is_deferred_true_for_angzarr_deferred(self) -> None:
        proto = CommandPage()
        proto.header.sequence = 1
        proto.header.angzarr_deferred.SetInParent()
        assert CommandPageW(proto).is_deferred() is True

    def test_type_url_returns_url(self) -> None:
        proto = CommandPage()
        proto.header.sequence = 1
        proto.command.Pack(Cover(domain="x"))
        url = CommandPageW(proto).type_url()
        assert url is not None and url.endswith("angzarr_client.proto.angzarr.Cover")

    def test_type_url_none_when_no_command(self) -> None:
        proto = CommandPage()
        proto.header.sequence = 1
        assert CommandPageW(proto).type_url() is None

    def test_payload_returns_bytes(self) -> None:
        proto = CommandPage()
        proto.header.sequence = 1
        proto.command.Pack(Cover(domain="x"))
        assert CommandPageW(proto).payload() is not None

    def test_payload_none_when_no_command(self) -> None:
        proto = CommandPage()
        proto.header.sequence = 1
        assert CommandPageW(proto).payload() is None

    def test_merge_strategy_default(self) -> None:
        from angzarr_client.proto.angzarr import MergeStrategy

        assert (
            CommandPageW(CommandPage()).merge_strategy()
            == MergeStrategy.MERGE_COMMUTATIVE
        )


class TestWrapperAttributeAccess:
    """Tests for delegated attribute access to underlying proto."""

    def test_event_book_w_delegates_proto_fields(self) -> None:
        """EventBookW allows access to proto fields via wrapper."""
        proto = EventBook()
        proto.next_sequence = 10
        wrapper = EventBookW(proto)
        # Direct proto access still works
        assert wrapper.proto.next_sequence == 10

    def test_cover_w_delegates_proto_fields(self) -> None:
        """CoverW allows access to proto fields via wrapper."""
        proto = Cover(domain="test", correlation_id="abc")
        wrapper = CoverW(proto)
        # Direct proto access still works
        assert wrapper.proto.domain == "test"
        assert wrapper.proto.correlation_id == "abc"
