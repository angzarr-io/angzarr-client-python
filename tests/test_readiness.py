"""Tests for angzarr_client.readiness — Audit #68 port of Rust readiness.rs."""

import os
import socket
import time
from unittest.mock import MagicMock

import pytest

from angzarr_client.readiness import (
    DEFAULT_PROBE_INTERVAL,
    DEFAULT_PROBE_TIMEOUT,
    OutputDomainProbe,
    Probe,
    TransportProbe,
    probe_config_from_env,
    run_supervisor,
)


class TestProbeConfigFromEnv:
    def test_defaults_when_unset(self, monkeypatch) -> None:
        monkeypatch.delenv("ANGZARR_READINESS_PROBE_INTERVAL", raising=False)
        monkeypatch.delenv("ANGZARR_READINESS_PROBE_TIMEOUT", raising=False)
        interval, timeout = probe_config_from_env()
        assert interval == DEFAULT_PROBE_INTERVAL
        assert timeout == DEFAULT_PROBE_TIMEOUT

    def test_reads_env_overrides(self, monkeypatch) -> None:
        monkeypatch.setenv("ANGZARR_READINESS_PROBE_INTERVAL", "5")
        monkeypatch.setenv("ANGZARR_READINESS_PROBE_TIMEOUT", "1")
        interval, timeout = probe_config_from_env()
        assert interval == 5.0
        assert timeout == 1.0

    def test_bad_value_falls_back_silently(self, monkeypatch) -> None:
        # Mirrors Rust's `.parse::<u64>().ok().unwrap_or(default)` —
        # non-numeric input doesn't crash, just falls through to default.
        monkeypatch.setenv("ANGZARR_READINESS_PROBE_INTERVAL", "not-a-number")
        monkeypatch.setenv("ANGZARR_READINESS_PROBE_TIMEOUT", "garbage")
        interval, timeout = probe_config_from_env()
        assert interval == DEFAULT_PROBE_INTERVAL
        assert timeout == DEFAULT_PROBE_TIMEOUT


class TestTransportProbe:
    def test_initially_false(self) -> None:
        probe, _signal = TransportProbe.new()
        assert probe.check(timeout=0.1) is False

    def test_flips_true_after_signal(self) -> None:
        probe, signal = TransportProbe.new()
        signal.mark_bound()
        assert probe.check(timeout=0.1) is True

    def test_name_is_transport(self) -> None:
        probe, _signal = TransportProbe.new()
        assert probe.name() == "transport"

    def test_signal_is_one_shot(self) -> None:
        # mark_bound is idempotent; subsequent calls don't break anything.
        probe, signal = TransportProbe.new()
        signal.mark_bound()
        signal.mark_bound()
        assert probe.check(timeout=0.1) is True


class TestOutputDomainProbe:
    def test_name_is_domain(self, monkeypatch) -> None:
        # Use distributed mode so resolve_ch_endpoint produces a TCP
        # host:port — no UDS path lookup required.
        monkeypatch.setenv("ANGZARR_MODE", "distributed")
        probe = OutputDomainProbe.for_domain("orders")
        assert probe.name() == "orders"

    def test_check_fails_when_endpoint_unreachable(self, monkeypatch) -> None:
        # Point at a port nothing's listening on; check must return
        # False within the timeout, not raise.
        monkeypatch.setenv("ANGZARR_MODE", "distributed")
        monkeypatch.setenv("ANGZARR_NAMESPACE", "nonexistent-ns-xyz")
        monkeypatch.setenv("ANGZARR_CH_PORT", "1")  # privileged, refused
        probe = OutputDomainProbe.for_domain("orders")
        assert probe.check(timeout=0.5) is False

    def test_check_succeeds_against_real_listener(
        self, monkeypatch, unused_tcp_port: int
    ) -> None:
        # Start a real TCP listener; resolve_ch_endpoint can't point at
        # `localhost`, so we monkeypatch the constructor's stored
        # tcp_addr directly.
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", unused_tcp_port))
        listener.listen(1)
        try:
            monkeypatch.setenv("ANGZARR_MODE", "distributed")
            probe = OutputDomainProbe.for_domain("anywhere")
            # Override the resolved addr to point at our listener.
            probe._uds_path = None
            probe._tcp_addr = f"127.0.0.1:{unused_tcp_port}"
            assert probe.check(timeout=1.0) is True
        finally:
            listener.close()

    def test_check_succeeds_against_real_uds(self, monkeypatch, tmp_path) -> None:
        sock_path = str(tmp_path / "probe.sock")
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(sock_path)
        listener.listen(1)
        try:
            monkeypatch.setenv("ANGZARR_MODE", "standalone")
            probe = OutputDomainProbe.for_domain("anywhere")
            probe._uds_path = sock_path
            probe._tcp_addr = None
            assert probe.check(timeout=1.0) is True
        finally:
            listener.close()
            os.unlink(sock_path)


class TestRunSupervisor:
    def test_all_ok_publishes_serving(self) -> None:
        # Synthetic always-true probe. Use a short interval so the
        # supervisor publishes before we check.
        from grpc_health.v1 import health_pb2

        class AlwaysOk(Probe):
            def name(self) -> str:
                return "ok"

            def check(self, timeout: float) -> bool:
                return True

        health = MagicMock()
        thread = run_supervisor([AlwaysOk()], health, [""], interval=0.05, timeout=0.1)
        try:
            time.sleep(0.15)
            # Should have been called at least once with SERVING.
            calls = [c.args for c in health.set.call_args_list]
            assert ("", health_pb2.HealthCheckResponse.SERVING) in calls
        finally:
            thread.stop()
            thread.join(timeout=1.0)

    def test_one_failing_probe_publishes_not_serving(self) -> None:
        from grpc_health.v1 import health_pb2

        class AlwaysOk(Probe):
            def name(self) -> str:
                return "ok"

            def check(self, timeout: float) -> bool:
                return True

        class AlwaysBad(Probe):
            def name(self) -> str:
                return "bad"

            def check(self, timeout: float) -> bool:
                return False

        health = MagicMock()
        thread = run_supervisor(
            [AlwaysOk(), AlwaysBad()],
            health,
            [""],
            interval=0.05,
            timeout=0.1,
        )
        try:
            time.sleep(0.15)
            calls = [c.args for c in health.set.call_args_list]
            assert ("", health_pb2.HealthCheckResponse.NOT_SERVING) in calls
            assert (
                "",
                health_pb2.HealthCheckResponse.SERVING,
            ) not in calls
        finally:
            thread.stop()
            thread.join(timeout=1.0)

    def test_probe_exception_treated_as_not_ok(self) -> None:
        # Audit #68: probe exceptions must not crash the supervisor.
        from grpc_health.v1 import health_pb2

        class Crashes(Probe):
            def name(self) -> str:
                return "crashes"

            def check(self, timeout: float) -> bool:
                raise RuntimeError("probe blew up")

        health = MagicMock()
        thread = run_supervisor([Crashes()], health, [""], interval=0.05, timeout=0.1)
        try:
            time.sleep(0.15)
            assert thread.is_alive()  # supervisor still running
            calls = [c.args for c in health.set.call_args_list]
            assert ("", health_pb2.HealthCheckResponse.NOT_SERVING) in calls
        finally:
            thread.stop()
            thread.join(timeout=1.0)

    def test_stop_terminates_loop(self) -> None:
        class AlwaysOk(Probe):
            def name(self) -> str:
                return "ok"

            def check(self, timeout: float) -> bool:
                return True

        health = MagicMock()
        thread = run_supervisor(
            [AlwaysOk()],
            health,
            [""],
            interval=10.0,  # long interval; stop must short-circuit
            timeout=0.1,
        )
        thread.stop()
        thread.join(timeout=1.0)
        assert not thread.is_alive()

    def test_publishes_to_every_service_name(self) -> None:

        class AlwaysOk(Probe):
            def name(self) -> str:
                return "ok"

            def check(self, timeout: float) -> bool:
                return True

        health = MagicMock()
        thread = run_supervisor(
            [AlwaysOk()],
            health,
            ["", "angzarr_client.proto.angzarr.Test"],
            interval=0.05,
            timeout=0.1,
        )
        try:
            time.sleep(0.15)
            names = {c.args[0] for c in health.set.call_args_list}
            assert "" in names
            assert "angzarr_client.proto.angzarr.Test" in names
        finally:
            thread.stop()
            thread.join(timeout=1.0)


@pytest.fixture
def unused_tcp_port() -> int:
    """Find a free TCP port for the lifetime of one test."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port
