"""QueryBuilder defaults reached through QueryClient entry points."""

from unittest.mock import Mock

from angzarr_client.client import QueryClient


def test_query_domain_builds_cover_for_domain_without_root() -> None:
    query = QueryClient.from_stub(Mock()).query_domain("orders").build()

    assert query.cover.domain == "orders"
    assert not query.cover.HasField("root")


def test_unset_correlation_id_leaves_the_selector_unset() -> None:
    query = QueryClient.from_stub(Mock()).query_domain("orders").build()
    assert not query.HasField("correlation_id")
