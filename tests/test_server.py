"""Tests for angzarr_client.server common utilities."""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

from angzarr_client import server as srv


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    for k in (
        "TRANSPORT_TYPE",
        "UDS_BASE_PATH",
        "SERVICE_NAME",
        "DOMAIN",
        "SAGA_NAME",
        "PROJECTOR_NAME",
        "PORT",
    ):
        monkeypatch.delenv(k, raising=False)


class TestGetTransportConfig:
    def test_default_tcp_default_port(self) -> None:
        transport, address = srv.get_transport_config()
        assert transport == "tcp"
        assert address == "[::]:50052"

    def test_tcp_custom_port(self, monkeypatch) -> None:
        monkeypatch.setenv("PORT", "6000")
        transport, address = srv.get_transport_config()
        assert transport == "tcp"
        assert address == "[::]:6000"

    def test_uds_without_qualifier(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("TRANSPORT_TYPE", "uds")
        monkeypatch.setenv("UDS_BASE_PATH", str(tmp_path))
        transport, address = srv.get_transport_config()
        assert transport == "uds"
        assert address == f"unix:{tmp_path}/business.sock"

    def test_uds_with_domain_qualifier(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("TRANSPORT_TYPE", "uds")
        monkeypatch.setenv("UDS_BASE_PATH", str(tmp_path))
        monkeypatch.setenv("SERVICE_NAME", "business")
        monkeypatch.setenv("DOMAIN", "orders")
        _, address = srv.get_transport_config()
        assert address == f"unix:{tmp_path}/business-orders.sock"

    def test_uds_saga_name_fallback(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("TRANSPORT_TYPE", "uds")
        monkeypatch.setenv("UDS_BASE_PATH", str(tmp_path))
        monkeypatch.setenv("SERVICE_NAME", "saga")
        monkeypatch.setenv("SAGA_NAME", "order-fulfillment")
        _, address = srv.get_transport_config()
        assert address == f"unix:{tmp_path}/saga-order-fulfillment.sock"

    def test_uds_projector_name_fallback(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("TRANSPORT_TYPE", "uds")
        monkeypatch.setenv("UDS_BASE_PATH", str(tmp_path))
        monkeypatch.setenv("SERVICE_NAME", "projector")
        monkeypatch.setenv("PROJECTOR_NAME", "output")
        _, address = srv.get_transport_config()
        assert address == f"unix:{tmp_path}/projector-output.sock"

    def test_uds_removes_stale_socket(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("TRANSPORT_TYPE", "uds")
        monkeypatch.setenv("UDS_BASE_PATH", str(tmp_path))
        stale = tmp_path / "business.sock"
        stale.write_text("stale")
        assert stale.exists()
        srv.get_transport_config()
        assert not stale.exists()

    def test_uds_creates_parent_directory(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("TRANSPORT_TYPE", "uds")
        nested = tmp_path / "nested" / "sockets"
        monkeypatch.setenv("UDS_BASE_PATH", str(nested))
        srv.get_transport_config()
        assert nested.is_dir()

    def test_transport_type_case_insensitive(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("TRANSPORT_TYPE", "UDS")
        monkeypatch.setenv("UDS_BASE_PATH", str(tmp_path))
        transport, _ = srv.get_transport_config()
        assert transport == "uds"


class TestConfigureLogging:
    def test_calls_structlog_configure(self, monkeypatch) -> None:
        import structlog

        called = {}

        def fake_configure(**kwargs):
            called.update(kwargs)

        monkeypatch.setattr(structlog, "configure", fake_configure)
        srv.configure_logging()
        assert "processors" in called
        assert called["context_class"] is dict


class TestCreateServer:
    # Audit #68: create_server now returns (server, address, health_servicer)
    # so the per-kind runner can pass the health servicer to the
    # readiness supervisor.
    def test_tcp_default_builds_server(self, monkeypatch) -> None:
        add_servicer = MagicMock()
        servicer = object()
        server, address, health_servicer = srv.create_server(
            add_servicer_func=add_servicer,
            servicer=servicer,
            service_name="angzarr_client.proto.angzarr.Test",
        )
        assert add_servicer.call_count == 1
        assert address.startswith("[::]:")
        assert health_servicer is not None

    def test_uds_builds_server(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("TRANSPORT_TYPE", "uds")
        monkeypatch.setenv("UDS_BASE_PATH", str(tmp_path))
        add_servicer = MagicMock()
        servicer = object()
        server, address, _hs = srv.create_server(
            add_servicer_func=add_servicer, servicer=servicer
        )
        assert address.startswith("unix:")

    def test_no_service_name_skips_named_health(self, monkeypatch) -> None:
        add_servicer = MagicMock()
        server, _addr, _hs = srv.create_server(
            add_servicer_func=add_servicer,
            servicer=object(),
            service_name="",
        )
        assert add_servicer.call_count == 1

    def test_initial_health_state_is_not_serving(self, monkeypatch) -> None:
        # Audit #68: health starts NOT_SERVING; the supervisor flips it
        # to SERVING once probes pass. Pre-#68 behavior set it SERVING
        # immediately, which made K8s readiness gate vacuously true.
        from grpc_health.v1 import health_pb2

        add_servicer = MagicMock()
        _server, _addr, health_servicer = srv.create_server(
            add_servicer_func=add_servicer,
            servicer=object(),
            service_name="angzarr_client.proto.angzarr.Test",
        )
        # HealthServicer doesn't expose a public getter; reach into the
        # internal status map. Pin both the empty (overall) name and
        # the kind-specific name.
        statuses = getattr(health_servicer, "_server_status", None) or getattr(
            health_servicer, "_servicer_status", None
        )
        assert statuses is not None, "could not introspect HealthServicer state"
        assert statuses[""] == health_pb2.HealthCheckResponse.NOT_SERVING
        assert (
            statuses["angzarr_client.proto.angzarr.Test"]
            == health_pb2.HealthCheckResponse.NOT_SERVING
        )


class TestRunServerLogging:
    def test_sets_default_port_when_missing(self, monkeypatch) -> None:
        monkeypatch.delenv("PORT", raising=False)
        fake_server = MagicMock()
        fake_server.wait_for_termination.side_effect = KeyboardInterrupt
        monkeypatch.setattr(
            srv,
            "create_server",
            lambda *a, **kw: (fake_server, "[::]:9999", MagicMock()),
        )
        logger = MagicMock()
        with pytest.raises(KeyboardInterrupt):
            srv.run_server(
                add_servicer_func=lambda *a, **kw: None,
                servicer=object(),
                service_name="angzarr_client.proto.angzarr.Test",
                domain="orders",
                default_port="9999",
                logger=logger,
            )
        assert os.environ["PORT"] == "9999"
        logger.info.assert_called_once()
        fake_server.start.assert_called_once()

    def test_preserves_existing_port(self, monkeypatch) -> None:
        monkeypatch.setenv("PORT", "4242")
        fake_server = MagicMock()
        fake_server.wait_for_termination.side_effect = KeyboardInterrupt
        monkeypatch.setattr(
            srv,
            "create_server",
            lambda *a, **kw: (fake_server, "[::]:4242", MagicMock()),
        )
        with pytest.raises(KeyboardInterrupt):
            srv.run_server(
                add_servicer_func=lambda *a, **kw: None,
                servicer=object(),
                default_port="9999",
            )
        assert os.environ["PORT"] == "4242"

    def test_prints_when_no_logger(self, monkeypatch, capsys) -> None:
        fake_server = MagicMock()
        fake_server.wait_for_termination.side_effect = KeyboardInterrupt
        monkeypatch.setattr(
            srv,
            "create_server",
            lambda *a, **kw: (fake_server, "[::]:50052", MagicMock()),
        )
        with pytest.raises(KeyboardInterrupt):
            srv.run_server(
                add_servicer_func=lambda *a, **kw: None,
                servicer=object(),
                service_name="angzarr_client.proto.angzarr.Test",
                domain="orders",
            )
        out = capsys.readouterr().out
        assert "angzarr_client.proto.angzarr.Test" in out
        assert "orders" in out


class TestCleanupSocket:
    def test_removes_existing_socket(self, tmp_path) -> None:
        sock = tmp_path / "my.sock"
        sock.write_text("x")
        srv.cleanup_socket(str(sock))
        assert not sock.exists()

    def test_noop_when_empty_path(self) -> None:
        srv.cleanup_socket("")  # should not raise

    def test_noop_when_missing_file(self, tmp_path) -> None:
        srv.cleanup_socket(str(tmp_path / "never-created.sock"))

    def test_swallows_os_error(self, monkeypatch, tmp_path) -> None:
        sock = tmp_path / "sock"
        sock.write_text("x")

        def explode(_path):
            raise OSError("nope")

        monkeypatch.setattr(os, "remove", explode)
        srv.cleanup_socket(str(sock))  # must not raise


class TestServerConfig:
    def test_default_port_and_no_uds(self) -> None:
        cfg = srv.ServerConfig()
        assert cfg.port == 50052
        assert cfg.uds_path is None

    def test_from_env_default_when_empty(self) -> None:
        cfg = srv.ServerConfig.from_env(default_port=1234)
        assert cfg.port == 1234
        assert cfg.uds_path is None

    def test_from_env_port(self, monkeypatch) -> None:
        monkeypatch.setenv("PORT", "9999")
        cfg = srv.ServerConfig.from_env()
        assert cfg.port == 9999
        assert cfg.uds_path is None

    def test_from_env_grpc_port_fallback(self, monkeypatch) -> None:
        monkeypatch.setenv("GRPC_PORT", "8888")
        cfg = srv.ServerConfig.from_env()
        assert cfg.port == 8888

    def test_from_env_uds_mode(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("UDS_BASE_PATH", str(tmp_path))
        monkeypatch.setenv("SERVICE_NAME", "business")
        monkeypatch.setenv("DOMAIN", "player")
        cfg = srv.ServerConfig.from_env()
        assert cfg.uds_path == str(tmp_path / "business-player.sock")

    def test_from_env_uds_partial_env_falls_back_to_tcp(self, monkeypatch) -> None:
        # UDS mode requires all three env vars; missing any → TCP.
        monkeypatch.setenv("UDS_BASE_PATH", "/tmp/x")
        monkeypatch.setenv("SERVICE_NAME", "business")
        cfg = srv.ServerConfig.from_env(default_port=7777)
        assert cfg.uds_path is None
        assert cfg.port == 7777

    def test_from_env_invalid_port_falls_back(self, monkeypatch) -> None:
        monkeypatch.setenv("PORT", "not-a-number")
        cfg = srv.ServerConfig.from_env(default_port=2222)
        assert cfg.port == 2222
