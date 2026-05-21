"""Tests for protobuf wrapper classes."""

from uuid import UUID as PyUUID


from angzarr_client.helpers import (
    UNKNOWN_DOMAIN,
    uuid_to_proto,
)
from angzarr_client.proto.angzarr import (
    CommandBook as CommandBookProto,
    CommandPage as CommandPageProto,
    CommandResponse as CommandResponseProto,
    Cover as CoverProto,
    EventBook as EventBookProto,
    EventPage as EventPageProto,
    PageHeader,
    Query as QueryProto,
)
from angzarr_client.wrappers import (
    CommandBook,
    CommandPage,
    CommandResponse,
    Cover,
    EventBook,
    EventPage,
    Query,
)


class TestEventBookW:
    """Tests for EventBook wrapper class."""

    def test_constructor_accepts_proto(self) -> None:
        """Wrapper accepts EventBook proto in constructor."""
        proto = EventBookProto()
        wrapper = EventBook(proto)
        assert wrapper.proto() is proto

    def test_next_sequence_returns_value(self) -> None:
        """next_sequence returns the next_sequence field."""
        proto = EventBookProto()
        proto.next_sequence = 5
        wrapper = EventBook(proto)
        assert wrapper.next_sequence() == 5

    def test_next_sequence_default_zero(self) -> None:
        """next_sequence returns 0 for new EventBook."""
        wrapper = EventBook(EventBookProto())
        assert wrapper.next_sequence() == 0

    def test_pages_returns_wrapped_list(self) -> None:
        """pages returns event pages as wrapped EventPage instances."""
        proto = EventBookProto()
        page1 = EventPageProto(header=PageHeader(sequence=1))
        page2 = EventPageProto(header=PageHeader(sequence=2))
        proto.pages.extend([page1, page2])
        wrapper = EventBook(proto)
        result = wrapper.pages()
        assert len(result) == 2
        assert isinstance(result[0], EventPage)
        assert isinstance(result[1], EventPage)
        assert result[0].proto().header.sequence == 1
        assert result[1].proto().header.sequence == 2

    def test_pages_returns_empty_list_when_none(self) -> None:
        """pages returns empty list for new EventBook."""
        wrapper = EventBook(EventBookProto())
        assert wrapper.pages() == []

    def test_domain_returns_domain_from_cover(self) -> None:
        """domain returns domain from embedded cover."""
        proto = EventBookProto()
        proto.cover.domain = "orders"
        wrapper = EventBook(proto)
        assert wrapper.domain() == "orders"

    def test_domain_returns_unknown_when_not_set(self) -> None:
        """domain returns UNKNOWN_DOMAIN when cover not set."""
        wrapper = EventBook(EventBookProto())
        assert wrapper.domain() == UNKNOWN_DOMAIN

    def test_correlation_id_returns_value(self) -> None:
        """correlation_id returns value from cover."""
        proto = EventBookProto()
        proto.cover.correlation_id = "corr-123"
        wrapper = EventBook(proto)
        assert wrapper.correlation_id() == "corr-123"

    def test_correlation_id_returns_empty_when_not_set(self) -> None:
        """correlation_id returns empty string when not set."""
        wrapper = EventBook(EventBookProto())
        assert wrapper.correlation_id() == ""

    def test_has_correlation_id_true(self) -> None:
        """has_correlation_id returns True when set."""
        proto = EventBookProto()
        proto.cover.correlation_id = "xyz"
        wrapper = EventBook(proto)
        assert wrapper.has_correlation_id() is True

    def test_has_correlation_id_false(self) -> None:
        """has_correlation_id returns False when not set."""
        wrapper = EventBook(EventBookProto())
        assert wrapper.has_correlation_id() is False

    def test_root_uuid_returns_uuid(self) -> None:
        """root_uuid returns Python UUID from cover."""
        test_uuid = PyUUID("12345678-1234-5678-1234-567812345678")
        proto = EventBookProto()
        proto.cover.root.CopyFrom(uuid_to_proto(test_uuid))
        wrapper = EventBook(proto)
        assert wrapper.root_uuid() == test_uuid

    def test_root_uuid_returns_none_when_not_set(self) -> None:
        """root_uuid returns None when root not set."""
        wrapper = EventBook(EventBookProto())
        assert wrapper.root_uuid() is None

    def test_edition_returns_edition_name(self) -> None:
        """edition returns edition name from cover."""
        proto = EventBookProto()
        proto.cover.edition.name = "v2"
        wrapper = EventBook(proto)
        assert wrapper.edition() == "v2"

    def test_edition_returns_none_when_not_set(self) -> None:
        """edition returns None when not set."""
        wrapper = EventBook(EventBookProto())
        assert wrapper.edition() is None

    def test_routing_key_returns_domain(self) -> None:
        """routing_key returns the domain."""
        proto = EventBookProto()
        proto.cover.domain = "inventory"
        wrapper = EventBook(proto)
        assert wrapper.routing_key() == "inventory"

    def test_cache_key_returns_domain_and_root(self) -> None:
        """cache_key returns domain:root_hex format when no edition."""
        test_uuid = PyUUID("12345678-1234-5678-1234-567812345678")
        proto = EventBookProto()
        proto.cover.domain = "orders"
        proto.cover.root.CopyFrom(uuid_to_proto(test_uuid))
        wrapper = EventBook(proto)
        assert wrapper.cache_key() == f":orders:{test_uuid.bytes.hex()}"

    def test_cover_wrapper_returns_cover_w(self) -> None:
        """cover_wrapper returns a Cover wrapping the cover."""
        proto = EventBookProto()
        proto.cover.domain = "test"
        wrapper = EventBook(proto)
        cover_w = wrapper.cover()
        assert isinstance(cover_w, Cover)
        assert cover_w.domain() == "test"


class TestCommandBookW:
    """Tests for CommandBook wrapper class."""

    def test_constructor_accepts_proto(self) -> None:
        """Wrapper accepts CommandBook proto in constructor."""
        proto = CommandBookProto()
        wrapper = CommandBook(proto)
        assert wrapper.proto() is proto

    def test_pages_returns_wrapped_list(self) -> None:
        """pages returns command pages as wrapped CommandPage instances."""
        proto = CommandBookProto()
        page1 = CommandPageProto()
        page1.header.sequence = 1
        page2 = CommandPageProto()
        page2.header.sequence = 2
        proto.pages.extend([page1, page2])
        wrapper = CommandBook(proto)
        result = wrapper.pages()
        assert len(result) == 2
        assert isinstance(result[0], CommandPage)
        assert result[0].sequence_num() == 1

    def test_pages_returns_empty_list_when_none(self) -> None:
        """pages returns empty list for new CommandBook."""
        wrapper = CommandBook(CommandBookProto())
        assert wrapper.pages() == []

    def test_domain_returns_domain_from_cover(self) -> None:
        """domain returns domain from embedded cover."""
        proto = CommandBookProto()
        proto.cover.domain = "fulfillment"
        wrapper = CommandBook(proto)
        assert wrapper.domain() == "fulfillment"

    def test_correlation_id_returns_value(self) -> None:
        """correlation_id returns value from cover."""
        proto = CommandBookProto()
        proto.cover.correlation_id = "cmd-456"
        wrapper = CommandBook(proto)
        assert wrapper.correlation_id() == "cmd-456"


class TestCoverW:
    """Tests for Cover wrapper class."""

    def test_constructor_accepts_proto(self) -> None:
        """Wrapper accepts Cover proto in constructor."""
        proto = CoverProto(domain="test")
        wrapper = Cover(proto)
        assert wrapper.proto() is proto

    def test_domain_returns_domain(self) -> None:
        """domain returns the domain field."""
        wrapper = Cover(CoverProto(domain="orders"))
        assert wrapper.domain() == "orders"

    def test_domain_returns_unknown_for_empty(self) -> None:
        """domain returns UNKNOWN_DOMAIN for empty domain."""
        wrapper = Cover(CoverProto())
        assert wrapper.domain() == UNKNOWN_DOMAIN

    def test_correlation_id_returns_value(self) -> None:
        """correlation_id returns the correlation_id field."""
        wrapper = Cover(CoverProto(correlation_id="abc-123"))
        assert wrapper.correlation_id() == "abc-123"

    def test_correlation_id_returns_empty_for_unset(self) -> None:
        """correlation_id returns empty string if not set."""
        wrapper = Cover(CoverProto())
        assert wrapper.correlation_id() == ""

    def test_has_correlation_id_true(self) -> None:
        """has_correlation_id returns True when set."""
        wrapper = Cover(CoverProto(correlation_id="xyz"))
        assert wrapper.has_correlation_id() is True

    def test_has_correlation_id_false(self) -> None:
        """has_correlation_id returns False when empty."""
        wrapper = Cover(CoverProto())
        assert wrapper.has_correlation_id() is False

    def test_root_uuid_returns_uuid(self) -> None:
        """root_uuid returns Python UUID."""
        test_uuid = PyUUID("deadbeef-dead-beef-dead-beefdeadbeef")
        proto = CoverProto()
        proto.root.CopyFrom(uuid_to_proto(test_uuid))
        wrapper = Cover(proto)
        assert wrapper.root_uuid() == test_uuid

    def test_root_uuid_returns_none_when_not_set(self) -> None:
        """root_uuid returns None when root not set."""
        wrapper = Cover(CoverProto())
        assert wrapper.root_uuid() is None

    def test_root_uuid_returns_none_for_invalid_bytes(self) -> None:
        """root_uuid returns None for invalid UUID bytes."""
        proto = CoverProto()
        proto.root.value = b"invalid"
        wrapper = Cover(proto)
        assert wrapper.root_uuid() is None

    def test_root_id_hex_returns_hex(self) -> None:
        """root_id_hex returns hex string."""
        test_uuid = PyUUID("12345678-1234-5678-1234-567812345678")
        proto = CoverProto()
        proto.root.CopyFrom(uuid_to_proto(test_uuid))
        wrapper = Cover(proto)
        assert wrapper.root_id_hex() == test_uuid.bytes.hex()

    def test_root_id_hex_returns_empty_when_not_set(self) -> None:
        """root_id_hex returns empty string when root not set."""
        wrapper = Cover(CoverProto())
        assert wrapper.root_id_hex() == ""

    def test_edition_returns_name(self) -> None:
        """edition returns edition name."""
        proto = CoverProto()
        proto.edition.name = "speculative"
        wrapper = Cover(proto)
        assert wrapper.edition() == "speculative"

    def test_edition_returns_none_when_not_set(self) -> None:
        """edition returns None when not set."""
        wrapper = Cover(CoverProto())
        assert wrapper.edition() is None

    def test_routing_key_returns_domain(self) -> None:
        """routing_key returns the domain."""
        wrapper = Cover(CoverProto(domain="payments"))
        assert wrapper.routing_key() == "payments"

    def test_cache_key_format(self) -> None:
        """cache_key returns edition:domain:root_hex format."""
        test_uuid = PyUUID("12345678-1234-5678-1234-567812345678")
        proto = CoverProto(domain="inventory")
        proto.root.CopyFrom(uuid_to_proto(test_uuid))
        wrapper = Cover(proto)
        # No edition set → empty prefix
        expected = f":inventory:{test_uuid.bytes.hex()}"
        assert wrapper.cache_key() == expected


class TestQueryW:
    """Tests for Query wrapper class."""

    def test_constructor_accepts_proto(self) -> None:
        """Wrapper accepts Query proto in constructor."""
        proto = QueryProto()
        wrapper = Query(proto)
        assert wrapper.proto() is proto

    def test_domain_returns_domain_from_cover(self) -> None:
        """domain returns domain from embedded cover."""
        proto = QueryProto()
        proto.cover.domain = "shipping"
        wrapper = Query(proto)
        assert wrapper.domain() == "shipping"

    def test_correlation_id_returns_value(self) -> None:
        """correlation_id returns value from cover."""
        proto = QueryProto()
        proto.cover.correlation_id = "query-789"
        wrapper = Query(proto)
        assert wrapper.correlation_id() == "query-789"


class TestEventPageW:
    """Tests for EventPage wrapper class."""

    def test_constructor_accepts_proto(self) -> None:
        """Wrapper accepts EventPage proto in constructor."""
        proto = EventPageProto(header=PageHeader(sequence=5))
        wrapper = EventPage(proto)
        assert wrapper.proto() is proto

    def test_decode_typed_returns_message(self) -> None:
        """decode_typed returns decoded message when class matches the page's type URL."""
        cover = CoverProto(domain="test", correlation_id="abc")
        proto = EventPageProto(header=PageHeader(sequence=1))
        proto.event.Pack(cover)
        wrapper = EventPage(proto)

        result = wrapper.decode_typed(CoverProto)
        assert result is not None
        assert result.domain == "test"
        assert result.correlation_id == "abc"

    def test_decode_typed_returns_none_for_mismatch(self) -> None:
        """decode_typed returns None when the page's type URL doesn't match the class."""
        cover = CoverProto(domain="test")
        proto = EventPageProto(header=PageHeader(sequence=1))
        proto.event.Pack(cover)
        wrapper = EventPage(proto)

        # Mismatch: page carries Cover, ask for EventBookProto.
        result = wrapper.decode_typed(EventBookProto)
        assert result is None

    def test_decode_typed_returns_none_when_no_event(self) -> None:
        """decode_typed returns None when the page has no event payload."""
        wrapper = EventPage(EventPageProto(header=PageHeader(sequence=1)))
        result = wrapper.decode_typed(CoverProto)
        assert result is None


class TestCommandPageW:
    """Tests for CommandPage wrapper class."""

    def test_constructor_accepts_proto(self) -> None:
        """Wrapper accepts CommandPage proto in constructor."""
        proto = CommandPageProto()
        proto.header.sequence = 10
        wrapper = CommandPage(proto)
        assert wrapper.proto() is proto

    def test_sequence_returns_value(self) -> None:
        """sequence returns the sequence field."""
        proto = CommandPageProto()
        proto.header.sequence = 42
        wrapper = CommandPage(proto)
        assert wrapper.sequence_num() == 42


class TestCommandResponseW:
    """Tests for CommandResponse wrapper class."""

    def test_constructor_accepts_proto(self) -> None:
        """Wrapper accepts CommandResponse proto in constructor."""
        proto = CommandResponseProto()
        wrapper = CommandResponse(proto)
        assert wrapper.proto() is proto

    def test_events_book_returns_wrapped_event_book(self) -> None:
        """events_book returns EventBook when present."""
        proto = CommandResponseProto()
        proto.events.next_sequence = 5
        page = proto.events.pages.add()
        page.header.sequence = 1
        wrapper = CommandResponse(proto)
        book = wrapper.events_book()
        assert book is not None
        assert isinstance(book, EventBook)
        assert book.next_sequence() == 5

    def test_events_book_returns_none_when_not_set(self) -> None:
        """events_book returns None when events not set."""
        wrapper = CommandResponse(CommandResponseProto())
        assert wrapper.events_book() is None

    def test_events_returns_wrapped_pages_when_present(self) -> None:
        """events returns event pages as wrapped EventPage instances."""
        proto = CommandResponseProto()
        page1 = proto.events.pages.add()
        page1.header.sequence = 1
        page2 = proto.events.pages.add()
        page2.header.sequence = 2
        wrapper = CommandResponse(proto)
        result = wrapper.events()
        assert len(result) == 2
        assert isinstance(result[0], EventPage)
        assert isinstance(result[1], EventPage)

    def test_events_returns_empty_when_not_set(self) -> None:
        """events returns empty list when events not set."""
        wrapper = CommandResponse(CommandResponseProto())
        assert wrapper.events() == []


class TestEventBookWAdditional:
    """Additional EventBook coverage."""

    def test_is_empty_true_for_empty_book(self) -> None:
        assert EventBook(EventBookProto()).is_empty() is True

    def test_is_empty_false_with_pages(self) -> None:
        proto = EventBookProto()
        proto.pages.add().header.sequence = 1
        assert EventBook(proto).is_empty() is False

    def test_last_page_returns_last(self) -> None:
        proto = EventBookProto()
        proto.pages.add().header.sequence = 1
        proto.pages.add().header.sequence = 7
        page = EventBook(proto).last_page()
        assert page is not None
        assert page.proto().header.sequence == 7

    def test_last_page_none_when_empty(self) -> None:
        assert EventBook(EventBookProto()).last_page() is None

    def test_first_page_returns_first(self) -> None:
        proto = EventBookProto()
        proto.pages.add().header.sequence = 3
        proto.pages.add().header.sequence = 9
        page = EventBook(proto).first_page()
        assert page is not None
        assert page.proto().header.sequence == 3

    def test_first_page_none_when_empty(self) -> None:
        assert EventBook(EventBookProto()).first_page() is None

    def test_root_uuid_none_when_no_cover(self) -> None:
        assert EventBook(EventBookProto()).root_uuid() is None

    def test_root_uuid_invalid_bytes(self) -> None:
        proto = EventBookProto()
        proto.cover.root.value = b"too-short"
        assert EventBook(proto).root_uuid() is None

    def test_root_id_hex_from_cover(self) -> None:
        test_uuid = PyUUID("12345678-1234-5678-1234-567812345678")
        proto = EventBookProto()
        proto.cover.root.CopyFrom(uuid_to_proto(test_uuid))
        assert EventBook(proto).root_id_hex() == test_uuid.bytes.hex()

    def test_root_id_hex_empty_when_no_cover(self) -> None:
        assert EventBook(EventBookProto()).root_id_hex() == ""

    def test_edition_none_when_no_cover(self) -> None:
        assert EventBook(EventBookProto()).edition() is None

    def test_cover_wrapper_returns_empty_when_no_cover(self) -> None:
        cw = EventBook(EventBookProto()).cover()
        assert isinstance(cw, Cover)
        assert cw.domain() == UNKNOWN_DOMAIN


class TestCommandBookWAdditional:
    """Additional CommandBook coverage."""

    def test_command_sequence_from_first_page(self) -> None:
        proto = CommandBookProto()
        proto.pages.add().header.sequence = 42
        assert CommandBook(proto).command_sequence() == 42

    def test_command_sequence_zero_when_no_pages(self) -> None:
        assert CommandBook(CommandBookProto()).command_sequence() == 0

    def test_command_sequence_zero_when_page_has_no_header(self) -> None:
        proto = CommandBookProto()
        proto.pages.add()  # page without header set
        assert CommandBook(proto).command_sequence() == 0

    def test_first_command_returns_first(self) -> None:
        proto = CommandBookProto()
        proto.pages.add().header.sequence = 1
        assert CommandBook(proto).first_command() is not None

    def test_first_command_none_when_empty(self) -> None:
        assert CommandBook(CommandBookProto()).first_command() is None

    def test_merge_strategy_default_when_no_pages(self) -> None:
        from angzarr_client.proto.angzarr import MergeStrategy

        assert (
            CommandBook(CommandBookProto()).merge_strategy()
            == MergeStrategy.MERGE_COMMUTATIVE
        )

    def test_merge_strategy_from_first_page(self) -> None:
        from angzarr_client.proto.angzarr import MergeStrategy

        proto = CommandBookProto()
        page = proto.pages.add()
        page.merge_strategy = MergeStrategy.MERGE_STRICT
        assert CommandBook(proto).merge_strategy() == MergeStrategy.MERGE_STRICT

    def test_domain_unknown_when_no_cover(self) -> None:
        assert CommandBook(CommandBookProto()).domain() == UNKNOWN_DOMAIN

    def test_correlation_id_empty_when_no_cover(self) -> None:
        assert CommandBook(CommandBookProto()).correlation_id() == ""

    def test_has_correlation_id_true(self) -> None:
        proto = CommandBookProto()
        proto.cover.correlation_id = "corr"
        assert CommandBook(proto).has_correlation_id() is True

    def test_has_correlation_id_false(self) -> None:
        assert CommandBook(CommandBookProto()).has_correlation_id() is False

    def test_root_uuid_from_cover(self) -> None:
        test_uuid = PyUUID("12345678-1234-5678-1234-567812345678")
        proto = CommandBookProto()
        proto.cover.root.CopyFrom(uuid_to_proto(test_uuid))
        assert CommandBook(proto).root_uuid() == test_uuid

    def test_root_uuid_none_when_no_cover(self) -> None:
        assert CommandBook(CommandBookProto()).root_uuid() is None

    def test_root_uuid_none_for_invalid_bytes(self) -> None:
        proto = CommandBookProto()
        proto.cover.root.value = b"bad"
        assert CommandBook(proto).root_uuid() is None

    def test_edition_from_cover(self) -> None:
        proto = CommandBookProto()
        proto.cover.edition.name = "v1"
        assert CommandBook(proto).edition() == "v1"

    def test_edition_none_when_no_cover(self) -> None:
        assert CommandBook(CommandBookProto()).edition() is None

    def test_routing_key_is_domain(self) -> None:
        proto = CommandBookProto()
        proto.cover.domain = "orders"
        assert CommandBook(proto).routing_key() == "orders"

    def test_cache_key_with_root(self) -> None:
        # Audit #75: empty edition → empty leading segment, NOT literal "None".
        test_uuid = PyUUID("12345678-1234-5678-1234-567812345678")
        proto = CommandBookProto()
        proto.cover.domain = "orders"
        proto.cover.root.CopyFrom(uuid_to_proto(test_uuid))
        assert CommandBook(proto).cache_key() == f":orders:{test_uuid.bytes.hex()}"

    def test_cache_key_without_root(self) -> None:
        proto = CommandBookProto()
        proto.cover.domain = "orders"
        assert CommandBook(proto).cache_key() == ":orders:"

    def test_cache_key_matches_sibling_wrappers(self) -> None:
        # Audit #75: parity with Cover/EventBook formula across all book wrappers.
        from angzarr_client.wrappers import Cover

        proto = CommandBookProto()
        proto.cover.domain = "orders"
        proto.cover.edition.name = "alt"
        assert CommandBook(proto).cache_key() == Cover(proto.cover).cache_key()

    def test_cover_wrapper(self) -> None:
        proto = CommandBookProto()
        proto.cover.domain = "orders"
        assert CommandBook(proto).cover().domain() == "orders"

    def test_cover_wrapper_empty_when_no_cover(self) -> None:
        cw = CommandBook(CommandBookProto()).cover()
        assert cw.domain() == UNKNOWN_DOMAIN


class TestQueryWAdditional:
    """Additional Query coverage."""

    def test_domain_unknown_when_no_cover(self) -> None:
        assert Query(QueryProto()).domain() == UNKNOWN_DOMAIN

    def test_correlation_id_empty_when_no_cover(self) -> None:
        assert Query(QueryProto()).correlation_id() == ""

    def test_has_correlation_id_true_and_false(self) -> None:
        assert Query(QueryProto()).has_correlation_id() is False
        proto = QueryProto()
        proto.cover.correlation_id = "x"
        assert Query(proto).has_correlation_id() is True

    def test_root_uuid_from_cover(self) -> None:
        test_uuid = PyUUID("12345678-1234-5678-1234-567812345678")
        proto = QueryProto()
        proto.cover.root.CopyFrom(uuid_to_proto(test_uuid))
        assert Query(proto).root_uuid() == test_uuid

    def test_root_uuid_none_when_no_cover(self) -> None:
        assert Query(QueryProto()).root_uuid() is None

    def test_root_uuid_none_for_invalid_bytes(self) -> None:
        proto = QueryProto()
        proto.cover.root.value = b"bad"
        assert Query(proto).root_uuid() is None

    def test_routing_key_is_domain(self) -> None:
        proto = QueryProto()
        proto.cover.domain = "inv"
        assert Query(proto).routing_key() == "inv"

    # Audit #81: edition / root_id_hex / cache_key parity with sibling wrappers.

    def test_edition_returns_value(self) -> None:
        proto = QueryProto()
        proto.cover.edition.name = "alt"
        assert Query(proto).edition() == "alt"

    def test_edition_none_when_unset(self) -> None:
        assert Query(QueryProto()).edition() is None

    def test_root_id_hex_returns_value(self) -> None:
        test_uuid = PyUUID("12345678-1234-5678-1234-567812345678")
        proto = QueryProto()
        proto.cover.root.CopyFrom(uuid_to_proto(test_uuid))
        assert Query(proto).root_id_hex() == test_uuid.bytes.hex()

    def test_root_id_hex_empty_when_no_cover(self) -> None:
        assert Query(QueryProto()).root_id_hex() == ""

    def test_cache_key_full_shape(self) -> None:
        test_uuid = PyUUID("12345678-1234-5678-1234-567812345678")
        proto = QueryProto()
        proto.cover.domain = "inv"
        proto.cover.edition.name = "alt"
        proto.cover.root.CopyFrom(uuid_to_proto(test_uuid))
        assert Query(proto).cache_key() == f"alt:inv:{test_uuid.bytes.hex()}"

    def test_cache_key_empty_edition_no_root(self) -> None:
        proto = QueryProto()
        proto.cover.domain = "inv"
        assert Query(proto).cache_key() == ":inv:"

    def test_cover_wrapper_returns_empty_when_no_cover(self) -> None:
        assert Query(QueryProto()).cover().domain() == UNKNOWN_DOMAIN

    def test_cover_wrapper_wraps_cover(self) -> None:
        proto = QueryProto()
        proto.cover.domain = "inv"
        assert Query(proto).cover().domain() == "inv"


class TestEventPageWAdditional:
    """Additional EventPage coverage."""

    def test_sequence_num_returns_header_sequence(self) -> None:
        proto = EventPageProto(header=PageHeader(sequence=17))
        assert EventPage(proto).sequence_num() == 17

    def test_sequence_num_zero_when_no_header(self) -> None:
        assert EventPage(EventPageProto()).sequence_num() == 0

    def test_header_returns_header(self) -> None:
        proto = EventPageProto(header=PageHeader(sequence=3))
        h = EventPage(proto).header()
        assert h is not None
        assert h.sequence == 3

    def test_header_none_when_absent(self) -> None:
        assert EventPage(EventPageProto()).header() is None

    def test_is_deferred_false_when_no_header(self) -> None:
        assert EventPage(EventPageProto()).is_deferred() is False

    def test_is_deferred_false_when_no_deferred_subfield(self) -> None:
        assert (
            EventPage(EventPageProto(header=PageHeader(sequence=1))).is_deferred()
            is False
        )

    def test_is_deferred_true_for_external_deferred(self) -> None:
        proto = EventPageProto(header=PageHeader(sequence=1))
        proto.header.external_deferred.SetInParent()
        assert EventPage(proto).is_deferred() is True

    def test_is_deferred_true_for_angzarr_deferred(self) -> None:
        proto = EventPageProto(header=PageHeader(sequence=1))
        proto.header.angzarr_deferred.SetInParent()
        assert EventPage(proto).is_deferred() is True

    def test_type_url_returns_url(self) -> None:
        proto = EventPageProto(header=PageHeader(sequence=1))
        proto.event.Pack(CoverProto(domain="x"))
        url = EventPage(proto).type_url()
        assert url is not None and url.endswith("angzarr_client.proto.angzarr.v1.Cover")

    def test_type_url_none_when_no_event(self) -> None:
        assert (
            EventPage(EventPageProto(header=PageHeader(sequence=1))).type_url() is None
        )

    def test_payload_returns_bytes(self) -> None:
        proto = EventPageProto(header=PageHeader(sequence=1))
        proto.event.Pack(CoverProto(domain="x"))
        payload = EventPage(proto).payload()
        assert isinstance(payload, (bytes, bytearray))

    def test_payload_none_when_no_event(self) -> None:
        assert (
            EventPage(EventPageProto(header=PageHeader(sequence=1))).payload() is None
        )

    def test_decode_typed_returns_message(self) -> None:
        proto = EventPageProto(header=PageHeader(sequence=1))
        proto.event.Pack(CoverProto(domain="zzz"))
        result = EventPage(proto).decode_typed(CoverProto)
        assert result is not None and result.domain == "zzz"

    def test_decode_typed_none_when_no_event(self) -> None:
        assert (
            EventPage(EventPageProto(header=PageHeader(sequence=1))).decode_typed(
                CoverProto
            )
            is None
        )


class TestCommandPageWAdditional:
    """Additional CommandPage coverage."""

    def test_sequence_zero_when_no_header(self) -> None:
        assert CommandPage(CommandPageProto()).sequence_num() == 0

    def test_header_returns_header(self) -> None:
        proto = CommandPageProto()
        proto.header.sequence = 5
        h = CommandPage(proto).header()
        assert h is not None and h.sequence == 5

    def test_header_none_when_absent(self) -> None:
        assert CommandPage(CommandPageProto()).header() is None

    def test_is_deferred_false_when_no_header(self) -> None:
        assert CommandPage(CommandPageProto()).is_deferred() is False

    def test_is_deferred_true_for_external_deferred(self) -> None:
        proto = CommandPageProto()
        proto.header.sequence = 1
        proto.header.external_deferred.SetInParent()
        assert CommandPage(proto).is_deferred() is True

    def test_is_deferred_true_for_angzarr_deferred(self) -> None:
        proto = CommandPageProto()
        proto.header.sequence = 1
        proto.header.angzarr_deferred.SetInParent()
        assert CommandPage(proto).is_deferred() is True

    def test_type_url_returns_url(self) -> None:
        proto = CommandPageProto()
        proto.header.sequence = 1
        proto.command.Pack(CoverProto(domain="x"))
        url = CommandPage(proto).type_url()
        assert url is not None and url.endswith("angzarr_client.proto.angzarr.v1.Cover")

    def test_type_url_none_when_no_command(self) -> None:
        proto = CommandPageProto()
        proto.header.sequence = 1
        assert CommandPage(proto).type_url() is None

    def test_payload_returns_bytes(self) -> None:
        proto = CommandPageProto()
        proto.header.sequence = 1
        proto.command.Pack(CoverProto(domain="x"))
        assert CommandPage(proto).payload() is not None

    def test_payload_none_when_no_command(self) -> None:
        proto = CommandPageProto()
        proto.header.sequence = 1
        assert CommandPage(proto).payload() is None

    def test_merge_strategy_default(self) -> None:
        from angzarr_client.proto.angzarr import MergeStrategy

        assert (
            CommandPage(CommandPageProto()).merge_strategy()
            == MergeStrategy.MERGE_COMMUTATIVE
        )


class TestWrapperAttributeAccess:
    """Tests for delegated attribute access to underlying proto."""

    def test_event_book_w_delegates_proto_fields(self) -> None:
        """EventBook allows access to proto fields via wrapper."""
        proto = EventBookProto()
        proto.next_sequence = 10
        wrapper = EventBook(proto)
        # Direct proto access still works
        assert wrapper.proto().next_sequence == 10

    def test_cover_w_delegates_proto_fields(self) -> None:
        """Cover allows access to proto fields via wrapper."""
        proto = CoverProto(domain="test", correlation_id="abc")
        wrapper = Cover(proto)
        # Direct proto access still works
        assert wrapper.proto().domain == "test"
        assert wrapper.proto().correlation_id == "abc"
