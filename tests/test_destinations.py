"""Tests for angzarr_client.destinations."""

import pytest

from angzarr_client.destinations import Destinations
from angzarr_client.errors import InvalidArgumentError
from angzarr_client.proto.angzarr import types_pb2 as types


class TestDestinations:
    def test_sequence_for_returns_value(self) -> None:
        d = Destinations({"inventory": 5, "shipping": 10})
        assert d.sequence_for("inventory") == 5
        assert d.sequence_for("shipping") == 10

    def test_sequence_for_returns_none_for_unknown(self) -> None:
        d = Destinations({"inventory": 5})
        assert d.sequence_for("unknown") is None

    def test_has_domain(self) -> None:
        d = Destinations({"inventory": 5})
        assert d.has_domain("inventory") is True
        assert d.has_domain("other") is False

    def test_domains_lists_keys(self) -> None:
        d = Destinations({"a": 1, "b": 2})
        assert sorted(d.domains) == ["a", "b"]

    def test_from_proto_copies_dict(self) -> None:
        original = {"orders": 3}
        d = Destinations.from_proto(original)
        original["orders"] = 99
        assert d.sequence_for("orders") == 3

    def test_stamp_command_sets_sequence_on_existing_header(self) -> None:
        d = Destinations({"inventory": 42})
        cmd = types.CommandBook()
        page = cmd.pages.add()
        page.header.sequence = 0
        result = d.stamp_command(cmd, "inventory")
        assert result is cmd
        assert cmd.pages[0].header.sequence == 42

    def test_stamp_command_creates_header_when_absent(self) -> None:
        d = Destinations({"inventory": 7})
        cmd = types.CommandBook()
        cmd.pages.add()  # page with no header
        d.stamp_command(cmd, "inventory")
        assert cmd.pages[0].header.sequence == 7

    def test_stamp_command_applies_to_all_pages(self) -> None:
        d = Destinations({"inventory": 11})
        cmd = types.CommandBook()
        cmd.pages.add().header.sequence = 0
        cmd.pages.add().header.sequence = 0
        d.stamp_command(cmd, "inventory")
        assert cmd.pages[0].header.sequence == 11
        assert cmd.pages[1].header.sequence == 11

    def test_stamp_command_raises_for_missing_domain(self) -> None:
        # Audit #64: structured error with the dedicated
        # MISSING_DESTINATION_SEQUENCE code (was incorrectly
        # NO_HANDLER_REGISTERED placeholder before).
        from angzarr_client.error_codes import codes, keys, messages

        d = Destinations({"inventory": 1})
        cmd = types.CommandBook()
        cmd.pages.add().header.sequence = 0
        with pytest.raises(InvalidArgumentError) as exc:
            d.stamp_command(cmd, "orders")
        assert exc.value.code == codes.MISSING_DESTINATION_SEQUENCE
        assert exc.value.message == messages.MISSING_DESTINATION_SEQUENCE
        assert exc.value.details[keys.DOMAIN] == "orders"
