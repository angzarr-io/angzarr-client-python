"""Endpoint resolution and gRPC channel target construction."""

import grpc
import pytest

from angzarr_client.client import TransportMode, _create_channel, resolve_ch_endpoint

_ENV_VARS = ("ANGZARR_MODE", "ANGZARR_UDS_BASE", "ANGZARR_NAMESPACE", "ANGZARR_CH_PORT")


@pytest.fixture
def clean_env(monkeypatch):
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


class TestResolveChEndpoint:
    def test_unset_mode_defaults_to_distributed_dns(self, clean_env) -> None:
        assert resolve_ch_endpoint("player") == "ch-player.angzarr.svc:1310"

    def test_standalone_default_socket_path(self, clean_env) -> None:
        assert (
            resolve_ch_endpoint("player", TransportMode.STANDALONE)
            == "/tmp/angzarr/ch-player.sock"
        )

    def test_standalone_mode_from_env(self, clean_env) -> None:
        clean_env.setenv("ANGZARR_MODE", "standalone")
        clean_env.setenv("ANGZARR_UDS_BASE", "/run/az")
        assert resolve_ch_endpoint("table") == "/run/az/ch-table.sock"

    def test_distributed_namespace_from_env(self, clean_env) -> None:
        clean_env.setenv("ANGZARR_NAMESPACE", "poker-prod")
        assert (
            resolve_ch_endpoint("hand", TransportMode.DISTRIBUTED)
            == "ch-hand.poker-prod.svc:1310"
        )

    def test_distributed_port_from_env(self, clean_env) -> None:
        clean_env.setenv("ANGZARR_CH_PORT", "9443")
        assert (
            resolve_ch_endpoint("hand", TransportMode.DISTRIBUTED)
            == "ch-hand.angzarr.svc:9443"
        )

    def test_distributed_explicit_namespace_argument(self, clean_env) -> None:
        assert (
            resolve_ch_endpoint("hand", TransportMode.DISTRIBUTED, namespace="qa")
            == "ch-hand.qa.svc:1310"
        )


@pytest.fixture
def channel_calls(monkeypatch):
    """Record the targets and credentials handed to grpc channel factories."""
    calls: list[tuple] = []
    creds = object()
    monkeypatch.setattr(
        grpc, "insecure_channel", lambda target: calls.append(("insecure", target))
    )
    monkeypatch.setattr(
        grpc,
        "secure_channel",
        lambda target, c: calls.append(("secure", target, c)),
    )
    monkeypatch.setattr(grpc, "ssl_channel_credentials", lambda: creds)
    return calls, creds


class TestCreateChannel:
    def test_relative_path_becomes_unix_uri_without_authority(
        self, channel_calls
    ) -> None:
        calls, _ = channel_calls
        _create_channel("./run/ch.sock")
        assert calls == [("insecure", "unix:./run/ch.sock")]

    def test_absolute_path_becomes_unix_uri_with_empty_authority(
        self, channel_calls
    ) -> None:
        calls, _ = channel_calls
        _create_channel("/tmp/angzarr/ch.sock")
        assert calls == [("insecure", "unix:///tmp/angzarr/ch.sock")]

    def test_unix_uri_passes_through(self, channel_calls) -> None:
        calls, _ = channel_calls
        _create_channel("unix:///tmp/x.sock")
        assert calls == [("insecure", "unix:///tmp/x.sock")]

    def test_https_uses_tls_channel_with_system_roots(self, channel_calls) -> None:
        calls, creds = channel_calls
        _create_channel("https://api.example.com:443")
        assert calls == [("secure", "api.example.com:443", creds)]

    def test_https_strips_only_trailing_slashes(self, channel_calls) -> None:
        calls, creds = channel_calls
        _create_channel("https://SANDBOX/")
        _create_channel("https://api.example.com:443/ ")
        assert calls == [
            ("secure", "SANDBOX", creds),
            ("secure", "api.example.com:443/ ", creds),
        ]

    def test_http_uses_insecure_channel_without_scheme(self, channel_calls) -> None:
        calls, _ = channel_calls
        _create_channel("http://SANDBOX/")
        _create_channel("http://localhost:1310/ ")
        assert calls == [
            ("insecure", "SANDBOX"),
            ("insecure", "localhost:1310/ "),
        ]

    def test_bare_host_port_is_tcp(self, channel_calls) -> None:
        calls, _ = channel_calls
        _create_channel("localhost:1310")
        assert calls == [("insecure", "localhost:1310")]
