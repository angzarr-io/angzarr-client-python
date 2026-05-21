"""Tests for angzarr_client.server common utilities."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock

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

    # Audit #77: ANGZARR_BIND_ADDRESS overrides the default
    # `[::]:{port}` composition.

    def test_bind_address_env_override(self, monkeypatch) -> None:
        monkeypatch.setenv(srv.ENV_BIND_ADDRESS, "0.0.0.0:9090")
        transport, address = srv.get_transport_config()
        assert transport == "tcp"
        assert address == "0.0.0.0:9090"

    def test_bind_address_env_override_ignores_port(self, monkeypatch) -> None:
        # When ANGZARR_BIND_ADDRESS is set, PORT is irrelevant.
        monkeypatch.setenv("PORT", "6000")
        monkeypatch.setenv(srv.ENV_BIND_ADDRESS, "127.0.0.1:7777")
        _, address = srv.get_transport_config()
        assert address == "127.0.0.1:7777"

    def test_bind_address_uds_unaffected(self, monkeypatch, tmp_path) -> None:
        # UDS path doesn't read the bind-address override.
        monkeypatch.setenv("TRANSPORT_TYPE", "uds")
        monkeypatch.setenv("UDS_BASE_PATH", str(tmp_path))
        monkeypatch.setenv(srv.ENV_BIND_ADDRESS, "0.0.0.0:9090")
        transport, address = srv.get_transport_config()
        assert transport == "uds"
        assert address.startswith("unix:")


class TestResolveBindAddress:
    """Audit #77: helper exposed at the crate root for symmetry with
    Rust's ``resolve_bind_address``."""

    def test_default_is_dual_stack(self, monkeypatch) -> None:
        monkeypatch.delenv(srv.ENV_BIND_ADDRESS, raising=False)
        monkeypatch.delenv("PORT", raising=False)
        assert srv.resolve_bind_address() == "[::]:50052"

    def test_default_with_explicit_port(self, monkeypatch) -> None:
        monkeypatch.delenv(srv.ENV_BIND_ADDRESS, raising=False)
        monkeypatch.delenv("PORT", raising=False)
        assert srv.resolve_bind_address(8080) == "[::]:8080"

    def test_port_env_beats_default_port_arg(self, monkeypatch) -> None:
        monkeypatch.delenv(srv.ENV_BIND_ADDRESS, raising=False)
        monkeypatch.setenv("PORT", "6000")
        # PORT env overrides default_port arg when no full address is set.
        assert srv.resolve_bind_address(50052) == "[::]:6000"

    def test_env_override_verbatim_ipv4(self, monkeypatch) -> None:
        monkeypatch.setenv(srv.ENV_BIND_ADDRESS, "127.0.0.1:9090")
        assert srv.resolve_bind_address(50052) == "127.0.0.1:9090"

    def test_env_override_verbatim_ipv6(self, monkeypatch) -> None:
        monkeypatch.setenv(srv.ENV_BIND_ADDRESS, "[::1]:8080")
        assert srv.resolve_bind_address(50052) == "[::1]:8080"

    def test_env_override_supersedes_port(self, monkeypatch) -> None:
        # Both set: full address wins; PORT is ignored.
        monkeypatch.setenv("PORT", "6000")
        monkeypatch.setenv(srv.ENV_BIND_ADDRESS, "0.0.0.0:1234")
        assert srv.resolve_bind_address() == "0.0.0.0:1234"


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


def _fake_server_handle(address: str = "[::]:9999"):
    """Build a ``ServerHandle`` whose ``server.wait_for_termination`` raises
    ``KeyboardInterrupt`` so ``run_server`` exits the asyncio loop cleanly.
    """
    fake_server = MagicMock()
    fake_server.start = AsyncMock()
    fake_server.wait_for_termination = AsyncMock(side_effect=KeyboardInterrupt)
    handle = srv.ServerHandle(
        server=fake_server,
        address=address,
        transport_signal=srv.TransportSignal(),
        health_servicer=AsyncMock(),
    )
    return handle, fake_server


class TestCreateServer:
    def test_tcp_default_builds_server(self, monkeypatch) -> None:
        add_servicer = MagicMock()
        servicer = object()
        handle = srv.create_server(
            add_servicer_func=add_servicer,
            servicer=servicer,
            service_name="angzarr_client.proto.angzarr.v1.Test",
        )
        assert add_servicer.call_count == 1
        assert handle.address.startswith("[::]:")
        assert isinstance(handle.transport_signal, srv.TransportSignal)
        # Health starts NOT_SERVING for both the overall ("") and named services.
        from grpc_health.v1 import health_pb2

        assert (
            handle.health_servicer._server_status[""]
            == health_pb2.HealthCheckResponse.NOT_SERVING
        )
        assert (
            handle.health_servicer._server_status[
                "angzarr_client.proto.angzarr.v1.Test"
            ]
            == health_pb2.HealthCheckResponse.NOT_SERVING
        )

    def test_uds_builds_server(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("TRANSPORT_TYPE", "uds")
        monkeypatch.setenv("UDS_BASE_PATH", str(tmp_path))
        add_servicer = MagicMock()
        servicer = object()
        handle = srv.create_server(add_servicer_func=add_servicer, servicer=servicer)
        assert handle.address.startswith("unix:")

    def test_no_service_name_skips_named_health(self, monkeypatch) -> None:
        add_servicer = MagicMock()
        handle = srv.create_server(
            add_servicer_func=add_servicer,
            servicer=object(),
            service_name="",
        )
        assert add_servicer.call_count == 1
        # Only the overall ("") name is registered; no per-service entry.
        assert list(handle.health_servicer._server_status.keys()) == [""]


class TestRunServerLogging:
    def test_sets_default_port_when_missing(self, monkeypatch) -> None:
        monkeypatch.delenv("PORT", raising=False)
        handle, fake_server = _fake_server_handle()
        monkeypatch.setattr(srv, "create_server", lambda *a, **kw: handle)
        logger = MagicMock()
        with pytest.raises(KeyboardInterrupt):
            srv.run_server(
                add_servicer_func=lambda *a, **kw: None,
                servicer=object(),
                service_name="angzarr_client.proto.angzarr.v1.Test",
                domain="orders",
                default_port="9999",
                logger=logger,
            )
        assert os.environ["PORT"] == "9999"
        # Audit #83: two info calls — `server_started` on the way in
        # and `server_shutdown` in the finally block.
        events = [c.args[0] for c in logger.info.call_args_list]
        assert events == ["server_started", "server_shutdown"]
        fake_server.start.assert_awaited_once()

    def test_preserves_existing_port(self, monkeypatch) -> None:
        monkeypatch.setenv("PORT", "4242")
        handle, _ = _fake_server_handle("[::]:4242")
        monkeypatch.setattr(srv, "create_server", lambda *a, **kw: handle)
        with pytest.raises(KeyboardInterrupt):
            srv.run_server(
                add_servicer_func=lambda *a, **kw: None,
                servicer=object(),
                default_port="9999",
            )
        assert os.environ["PORT"] == "4242"

    def test_prints_when_no_logger(self, monkeypatch, capsys) -> None:
        handle, _ = _fake_server_handle("[::]:50052")
        monkeypatch.setattr(srv, "create_server", lambda *a, **kw: handle)
        with pytest.raises(KeyboardInterrupt):
            srv.run_server(
                add_servicer_func=lambda *a, **kw: None,
                servicer=object(),
                service_name="angzarr_client.proto.angzarr.v1.Test",
                domain="orders",
            )
        out = capsys.readouterr().out
        assert "angzarr_client.proto.angzarr.v1.Test" in out
        assert "orders" in out

    def test_marks_transport_bound_after_start(self, monkeypatch) -> None:
        handle, fake_server = _fake_server_handle()
        signal = handle.transport_signal
        assert not signal.is_bound()
        monkeypatch.setattr(srv, "create_server", lambda *a, **kw: handle)
        with pytest.raises(KeyboardInterrupt):
            srv.run_server(
                add_servicer_func=lambda *a, **kw: None,
                servicer=object(),
                default_port="9999",
            )
        # The supervisor coroutine sees this signal as bound after start().
        assert signal.is_bound()
        fake_server.start.assert_awaited_once()

    def test_run_server_publishes_not_serving_on_shutdown(self, monkeypatch) -> None:
        # Audit #83: every registered health name flipped to NOT_SERVING
        # in the finally block after `wait_for_termination` returns.
        from grpc_health.v1 import health_pb2

        handle, _ = _fake_server_handle()
        monkeypatch.setattr(srv, "create_server", lambda *a, **kw: handle)
        with pytest.raises(KeyboardInterrupt):
            srv.run_server(
                add_servicer_func=lambda *a, **kw: None,
                servicer=object(),
                service_name="angzarr_client.proto.angzarr.v1.Test",
                domain="orders",
                default_port="9999",
            )
        # Every awaited call on the mock health_servicer is recorded;
        # we expect at least one set(name, NOT_SERVING) per registered
        # service name (overall "" and the explicit service_name).
        calls = handle.health_servicer.set.await_args_list
        publishes = [
            (args[0], args[1])
            for args, _ in (call for call in calls)
            if len(args) >= 2 and args[1] == health_pb2.HealthCheckResponse.NOT_SERVING
        ]
        published_names = {name for name, _ in publishes}
        assert "" in published_names
        assert "angzarr_client.proto.angzarr.v1.Test" in published_names


class TestPublishShutdownStatus:
    """Audit #83: helper flips every registered health name to
    NOT_SERVING so K8s drains the pod on shutdown."""

    async def test_flips_every_name(self) -> None:
        from grpc_health.v1 import health_pb2

        from angzarr_client.server import _publish_shutdown_status

        servicer = AsyncMock()
        await _publish_shutdown_status(servicer, ["", "svc.A", "svc.B"])

        calls = servicer.set.await_args_list
        assert len(calls) == 3
        for call, expected_name in zip(calls, ["", "svc.A", "svc.B"], strict=True):
            args, _ = call
            assert args[0] == expected_name
            assert args[1] == health_pb2.HealthCheckResponse.NOT_SERVING

    async def test_empty_names_is_noop(self) -> None:
        from angzarr_client.server import _publish_shutdown_status

        servicer = AsyncMock()
        await _publish_shutdown_status(servicer, [])
        servicer.set.assert_not_awaited()


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
