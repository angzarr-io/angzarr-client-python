"""Tests for angzarr_client.server common utilities."""

from __future__ import annotations

import json
import logging
import os
import types
from datetime import datetime

import pytest
import structlog

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


class TestUdsDefaultBasePath:
    """Without ``UDS_BASE_PATH`` the socket lives under ``/tmp/angzarr``. The
    module's filesystem calls are recorded rather than performed, so the test
    never touches a real ``/tmp/angzarr``."""

    @pytest.fixture
    def fs(self, monkeypatch):
        made: list[tuple[str, bool]] = []
        removed: list[str] = []
        fake_path = types.SimpleNamespace(
            dirname=os.path.dirname, exists=lambda path: True
        )
        fake_os = types.SimpleNamespace(
            environ=os.environ,
            path=fake_path,
            makedirs=lambda path, exist_ok=False: made.append((path, exist_ok)),
            remove=removed.append,
        )
        monkeypatch.setattr(srv, "os", fake_os)
        return made, removed

    def test_socket_defaults_under_tmp_angzarr(self, monkeypatch, fs) -> None:
        monkeypatch.setenv("TRANSPORT_TYPE", "uds")
        monkeypatch.setenv("DOMAIN", "orders")
        made, removed = fs
        assert srv.get_transport_config() == (
            "uds",
            "unix:/tmp/angzarr/business-orders.sock",
        )
        assert made == [("/tmp/angzarr", True)]
        assert removed == ["/tmp/angzarr/business-orders.sock"]


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


@pytest.fixture
def structlog_config():
    saved = structlog.get_config()
    yield
    structlog.configure(**saved)


class TestConfigureLoggingEffects:
    def test_replaces_a_prior_configuration_with_json_lines_on_stdout(
        self, structlog_config, capsys
    ) -> None:
        # A prior configuration that would drop or swallow everything.
        structlog.configure(
            processors=[],
            wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL),
            logger_factory=structlog.ReturnLoggerFactory(),
        )
        srv.configure_logging()
        log = structlog.get_logger()
        log.debug("probe_debug", n=1)
        log.info("probe_info", k="v")
        lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        assert [(r["event"], r["level"]) for r in lines] == [
            ("probe_debug", "debug"),
            ("probe_info", "info"),
        ]
        assert (lines[0]["n"], lines[1]["k"]) == (1, "v")
        assert set(lines[1]) == {"event", "level", "timestamp", "k"}
        for record in lines:
            assert isinstance(record["timestamp"], str)
            assert datetime.fromisoformat(record["timestamp"]).year >= 2024
