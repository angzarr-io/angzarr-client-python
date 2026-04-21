"""Tests for angzarr_client.protoname."""

from angzarr_client import protoname
from angzarr_client.proto.angzarr import types_pb2 as types


class TestProtoname:
    def test_name_from_class(self) -> None:
        assert protoname.name(types.Cover) == "Cover"

    def test_name_from_instance(self) -> None:
        assert protoname.name(types.Cover()) == "Cover"

    def test_type_url_from_class(self) -> None:
        assert protoname.type_url(types.Cover) == "type.examples/examples.Cover"

    def test_type_url_from_instance(self) -> None:
        assert protoname.type_url(types.Cover()) == "type.examples/examples.Cover"
