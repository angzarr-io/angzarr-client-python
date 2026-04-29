"""Tests for the readiness supervisor and probes.

Mirrors ``client-rust/main/src/readiness.rs`` semantics: TransportProbe
flips once the listener is bound (and never reverts); OutputDomainProbe
attempts a TCP/UDS connect on each tick and reports reachability;
``run_supervisor`` aggregates probes and publishes ``SERVING`` /
``NOT_SERVING`` to the named services on every tick.
"""

from __future__ import annotations

import asyncio

from grpc_health.v1 import health_pb2

from angzarr_client.readiness import (
    DEFAULT_PROBE_INTERVAL,
    DEFAULT_PROBE_TIMEOUT,
    BusProbe,
    OutputDomainProbe,
    Probe,
    TransportProbe,
    TransportSignal,
    _parse_endpoint,
    _TcpEndpoint,
    _UdsEndpoint,
    probe_config_from_env,
    run_supervisor,
)

SERVING = health_pb2.HealthCheckResponse.SERVING
NOT_SERVING = health_pb2.HealthCheckResponse.NOT_SERVING


# ---------------------------------------------------------------------------
# probe_config_from_env
# ---------------------------------------------------------------------------


class TestProbeConfigFromEnv:
    def test_defaults_when_unset(self, monkeypatch) -> None:
        monkeypatch.delenv("ANGZARR_READINESS_PROBE_INTERVAL", raising=False)
        monkeypatch.delenv("ANGZARR_READINESS_PROBE_TIMEOUT", raising=False)
        interval, timeout = probe_config_from_env()
        assert interval == DEFAULT_PROBE_INTERVAL
        assert timeout == DEFAULT_PROBE_TIMEOUT

    def test_reads_env(self, monkeypatch) -> None:
        monkeypatch.setenv("ANGZARR_READINESS_PROBE_INTERVAL", "5")
        monkeypatch.setenv("ANGZARR_READINESS_PROBE_TIMEOUT", "1.5")
        interval, timeout = probe_config_from_env()
        assert interval == 5.0
        assert timeout == 1.5

    def test_falls_back_on_garbage(self, monkeypatch) -> None:
        monkeypatch.setenv("ANGZARR_READINESS_PROBE_INTERVAL", "thirty")
        monkeypatch.setenv("ANGZARR_READINESS_PROBE_TIMEOUT", "two")
        interval, timeout = probe_config_from_env()
        assert interval == DEFAULT_PROBE_INTERVAL
        assert timeout == DEFAULT_PROBE_TIMEOUT


# ---------------------------------------------------------------------------
# TransportProbe / TransportSignal
# ---------------------------------------------------------------------------


class TestTransportProbe:
    async def test_starts_unbound(self) -> None:
        probe, signal = TransportProbe.new()
        assert probe.name == "transport"
        assert await probe.check() is False
        assert signal.is_bound() is False

    async def test_flips_after_signal_marked(self) -> None:
        probe, signal = TransportProbe.new()
        signal.mark_bound()
        assert await probe.check() is True

    async def test_signal_is_one_way(self) -> None:
        # Once bound, never reverts — mirrors Rust's `AtomicBool` set_only.
        probe, signal = TransportProbe.new()
        signal.mark_bound()
        assert await probe.check() is True
        # No public unmark; reusing the signal stays bound.
        assert signal.is_bound() is True
        assert await probe.check() is True


# ---------------------------------------------------------------------------
# Endpoint parsing
# ---------------------------------------------------------------------------


class TestParseEndpoint:
    def test_tcp_host_port(self) -> None:
        ep = _parse_endpoint("inventory:1310")
        assert ep == _TcpEndpoint("inventory:1310")

    def test_unix_prefix_is_uds(self) -> None:
        ep = _parse_endpoint("unix:/tmp/sock")
        assert ep == _UdsEndpoint("/tmp/sock")

    def test_unix_relative_is_uds(self) -> None:
        ep = _parse_endpoint("unix:relative.sock")
        assert ep == _UdsEndpoint("relative.sock")

    def test_leading_slash_is_uds(self) -> None:
        ep = _parse_endpoint("/var/run/angzarr.sock")
        assert ep == _UdsEndpoint("/var/run/angzarr.sock")


# ---------------------------------------------------------------------------
# OutputDomainProbe
# ---------------------------------------------------------------------------


class TestOutputDomainProbe:
    async def test_tcp_check_succeeds_when_listener_bound(
        self, unused_tcp_port
    ) -> None:
        async def handler(reader, writer):
            writer.close()

        server = await asyncio.start_server(handler, "127.0.0.1", unused_tcp_port)
        try:
            probe = OutputDomainProbe(
                "downstream", _TcpEndpoint(f"127.0.0.1:{unused_tcp_port}")
            )
            assert probe.name == "downstream"
            assert await probe.check() is True
        finally:
            server.close()
            await server.wait_closed()

    async def test_tcp_check_fails_when_nothing_listening(
        self, unused_tcp_port
    ) -> None:
        # No listener bound — connect should fail fast.
        probe = OutputDomainProbe(
            "downstream", _TcpEndpoint(f"127.0.0.1:{unused_tcp_port}")
        )
        assert await probe.check() is False

    async def test_tcp_check_fails_on_garbage_address(self) -> None:
        probe = OutputDomainProbe("garbage", _TcpEndpoint("not-a-host:notaport"))
        assert await probe.check() is False

    async def test_uds_check_succeeds_when_socket_bound(self, tmp_path) -> None:
        sock_path = str(tmp_path / "ready.sock")

        async def handler(reader, writer):
            writer.close()

        server = await asyncio.start_unix_server(handler, sock_path)
        try:
            probe = OutputDomainProbe("uds-downstream", _UdsEndpoint(sock_path))
            assert await probe.check() is True
        finally:
            server.close()
            await server.wait_closed()

    async def test_uds_check_fails_when_socket_missing(self, tmp_path) -> None:
        probe = OutputDomainProbe(
            "uds-downstream", _UdsEndpoint(str(tmp_path / "missing.sock"))
        )
        assert await probe.check() is False

    def test_for_domain_distributed_mode_resolves_to_tcp(self, monkeypatch) -> None:
        monkeypatch.setenv("ANGZARR_MODE", "distributed")
        monkeypatch.delenv("ANGZARR_CH_PORT", raising=False)
        probe = OutputDomainProbe.for_domain("inventory")
        assert probe.name == "inventory"
        assert isinstance(probe._endpoint, _TcpEndpoint)
        # Distributed mode uses the same Kubernetes service-name format
        # as the client (`ch-<domain>.angzarr.svc:<port>`).
        assert "inventory" in probe._endpoint.addr
        assert probe._endpoint.addr.endswith(":1310")

    def test_for_domain_standalone_mode_resolves_to_uds(self, monkeypatch) -> None:
        monkeypatch.setenv("ANGZARR_MODE", "standalone")
        probe = OutputDomainProbe.for_domain("inventory")
        assert isinstance(probe._endpoint, _UdsEndpoint)
        assert probe._endpoint.path.endswith("ch-inventory.sock")


# ---------------------------------------------------------------------------
# BusProbe
# ---------------------------------------------------------------------------


class TestBusProbe:
    def test_from_env_returns_none_when_unset(self, monkeypatch) -> None:
        monkeypatch.delenv("ANGZARR_BUS_ENDPOINT", raising=False)
        assert BusProbe.from_env() is None

    def test_from_env_returns_none_when_blank(self, monkeypatch) -> None:
        monkeypatch.setenv("ANGZARR_BUS_ENDPOINT", "")
        assert BusProbe.from_env() is None

    def test_from_env_parses_tcp(self, monkeypatch) -> None:
        monkeypatch.setenv("ANGZARR_BUS_ENDPOINT", "kafka.bus.svc:9092")
        probe = BusProbe.from_env()
        assert probe is not None
        assert probe.name == "bus"
        assert isinstance(probe._endpoint, _TcpEndpoint)
        assert probe._endpoint.addr == "kafka.bus.svc:9092"

    def test_from_env_parses_uds(self, monkeypatch) -> None:
        monkeypatch.setenv("ANGZARR_BUS_ENDPOINT", "unix:/var/run/bus.sock")
        probe = BusProbe.from_env()
        assert probe is not None
        assert isinstance(probe._endpoint, _UdsEndpoint)
        assert probe._endpoint.path == "/var/run/bus.sock"

    async def test_tcp_check_succeeds_when_listener_bound(
        self, unused_tcp_port
    ) -> None:
        async def handler(reader, writer):
            writer.close()

        server = await asyncio.start_server(handler, "127.0.0.1", unused_tcp_port)
        try:
            probe = BusProbe(_TcpEndpoint(f"127.0.0.1:{unused_tcp_port}"))
            assert await probe.check() is True
        finally:
            server.close()
            await server.wait_closed()

    async def test_tcp_check_fails_when_nothing_listening(
        self, unused_tcp_port
    ) -> None:
        probe = BusProbe(_TcpEndpoint(f"127.0.0.1:{unused_tcp_port}"))
        assert await probe.check() is False

    async def test_uds_check_succeeds_when_socket_bound(self, tmp_path) -> None:
        sock_path = str(tmp_path / "bus.sock")

        async def handler(reader, writer):
            writer.close()

        server = await asyncio.start_unix_server(handler, sock_path)
        try:
            probe = BusProbe(_UdsEndpoint(sock_path))
            assert await probe.check() is True
        finally:
            server.close()
            await server.wait_closed()


# ---------------------------------------------------------------------------
# run_supervisor
# ---------------------------------------------------------------------------


class _FakeProbe(Probe):
    def __init__(self, name: str, results: list[bool]) -> None:
        self._name = name
        self._results = list(results)
        self.calls = 0

    @property
    def name(self) -> str:
        return self._name

    async def check(self) -> bool:
        self.calls += 1
        if self._results:
            return self._results.pop(0)
        return False


class _SlowProbe(Probe):
    def __init__(self, name: str, delay: float) -> None:
        self._name = name
        self._delay = delay

    @property
    def name(self) -> str:
        return self._name

    async def check(self) -> bool:
        await asyncio.sleep(self._delay)
        return True


class _RecordingHealth:
    """Stand-in for ``grpc_health.v1.health.aio.HealthServicer`` that
    captures every status set so tests can assert the publish sequence.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    async def set(self, service: str, status) -> None:
        self.calls.append((service, status))


class TestRunSupervisor:
    async def test_publishes_serving_when_all_probes_pass(self) -> None:
        probe = _FakeProbe("p", [True] * 100)
        health = _RecordingHealth()
        task = asyncio.create_task(
            run_supervisor(
                probes=[probe],
                health_servicer=health,
                service_names=["", "MyService"],
                interval=0.01,
                timeout=1.0,
            )
        )
        # Yield long enough for at least one tick.
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert probe.calls >= 1
        # First tick should publish SERVING for both names.
        assert ("", SERVING) in health.calls
        assert ("MyService", SERVING) in health.calls
        # No NOT_SERVING — every probe returned True.
        assert all(status == SERVING for _, status in health.calls)

    async def test_publishes_not_serving_when_any_probe_fails(self) -> None:
        # Two probes; second always fails → aggregate is NOT_SERVING.
        probe_a = _FakeProbe("a", [True, True, True])
        probe_b = _FakeProbe("b", [False, False, False])
        health = _RecordingHealth()
        task = asyncio.create_task(
            run_supervisor(
                probes=[probe_a, probe_b],
                health_servicer=health,
                service_names=[""],
                interval=0.01,
                timeout=1.0,
            )
        )
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert probe_a.calls >= 1
        assert probe_b.calls >= 1
        assert all(status == NOT_SERVING for _, status in health.calls)

    async def test_treats_probe_timeout_as_failure(self) -> None:
        slow = _SlowProbe("slow", delay=1.0)
        health = _RecordingHealth()
        task = asyncio.create_task(
            run_supervisor(
                probes=[slow],
                health_servicer=health,
                service_names=[""],
                interval=0.01,
                timeout=0.01,
            )
        )
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert health.calls, "supervisor must publish at least one tick"
        assert all(status == NOT_SERVING for _, status in health.calls)

    async def test_loops_until_cancelled(self) -> None:
        probe = _FakeProbe("p", [True] * 100)
        health = _RecordingHealth()
        task = asyncio.create_task(
            run_supervisor(
                probes=[probe],
                health_servicer=health,
                service_names=[""],
                interval=0.005,
                timeout=1.0,
            )
        )
        await asyncio.sleep(0.05)
        ticks_so_far = probe.calls
        await asyncio.sleep(0.05)
        ticks_later = probe.calls
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert ticks_later > ticks_so_far


# ---------------------------------------------------------------------------
# Sanity: TransportSignal, isolated
# ---------------------------------------------------------------------------


class TestTransportSignal:
    def test_default_unbound(self) -> None:
        signal = TransportSignal()
        assert signal.is_bound() is False

    def test_mark_bound_flips(self) -> None:
        signal = TransportSignal()
        signal.mark_bound()
        assert signal.is_bound() is True

    def test_idempotent(self) -> None:
        signal = TransportSignal()
        signal.mark_bound()
        signal.mark_bound()
        assert signal.is_bound() is True


# ---------------------------------------------------------------------------
# Saga / PM router sync_output_domains() — drives probe selection.
# ---------------------------------------------------------------------------


class TestSagaRouterSyncOutputDomains:
    def test_empty_when_sync_false(self) -> None:
        from angzarr_client.router import Router, handles, saga

        @saga(name="s", source="order", target="inventory", sync=False)
        class S:
            @handles(int)
            def on(self, event, destinations):
                pass

        router = Router("s").with_handler(S, S).build()
        assert router.output_domains() == ["inventory"]
        assert router.sync_output_domains() == []

    def test_includes_target_when_sync_true(self) -> None:
        from angzarr_client.router import Router, handles, saga

        @saga(name="s", source="order", target="inventory", sync=True)
        class S:
            @handles(int)
            def on(self, event, destinations):
                pass

        router = Router("s").with_handler(S, S).build()
        assert router.output_domains() == ["inventory"]
        assert router.sync_output_domains() == ["inventory"]


class TestProcessManagerRouterSyncOutputDomains:
    def test_empty_when_no_sync_targets(self) -> None:
        from dataclasses import dataclass

        from angzarr_client.router import Router, handles, process_manager

        @dataclass
        class State:
            ok: bool = False

        @process_manager(
            name="pm",
            pm_domain="fulfillment",
            sources=["order"],
            targets=["inventory", "shipping"],
            state=State,
        )
        class PM:
            @handles(int)
            def on(self, event, state, destinations):
                pass

        router = Router("pm").with_handler(PM, PM).build()
        assert sorted(router.output_domains()) == ["inventory", "shipping"]
        assert router.sync_output_domains() == []

    def test_returns_only_sync_subset(self) -> None:
        from dataclasses import dataclass

        from angzarr_client.router import Router, handles, process_manager

        @dataclass
        class State:
            ok: bool = False

        @process_manager(
            name="pm",
            pm_domain="fulfillment",
            sources=["order"],
            targets=["inventory", "shipping"],
            sync_targets=["inventory"],
            state=State,
        )
        class PM:
            @handles(int)
            def on(self, event, state, destinations):
                pass

        router = Router("pm").with_handler(PM, PM).build()
        assert sorted(router.output_domains()) == ["inventory", "shipping"]
        assert router.sync_output_domains() == ["inventory"]

    def test_rejects_sync_target_not_in_targets(self) -> None:
        import pytest

        from angzarr_client.router import process_manager

        with pytest.raises(ValueError, match="not in targets"):

            @process_manager(
                name="pm",
                pm_domain="fulfillment",
                sources=["order"],
                targets=["inventory"],
                sync_targets=["shipping"],  # not in targets
                state=type("State", (), {}),
            )
            class PM:
                pass


class TestHasAsyncOutputs:
    """Per-handler check — must remain `True` even when a target's
    sync/async flags vary across handlers in the same router. This is
    the case the deduplicated set-difference would miss.
    """

    def test_saga_async_default(self) -> None:
        from angzarr_client.router import Router, handles, saga

        @saga(name="s", source="order", target="inventory")
        class S:
            @handles(int)
            def on(self, event):
                pass

        router = Router("s").with_handler(S, S).build()
        assert router.has_async_outputs() is True

    def test_saga_sync_only(self) -> None:
        from angzarr_client.router import Router, handles, saga

        @saga(name="s", source="order", target="inventory", sync=True)
        class S:
            @handles(int)
            def on(self, event):
                pass

        router = Router("s").with_handler(S, S).build()
        assert router.has_async_outputs() is False

    def test_saga_router_with_mixed_handlers_same_target(self) -> None:
        # Two sagas share the same target but disagree on sync. Naive
        # set-difference over output_domains would call this all-sync;
        # per-handler check reports has_async = True.
        from angzarr_client.router import Router, handles, saga

        @saga(name="sync-s", source="order", target="inventory", sync=True)
        class SyncS:
            @handles(int)
            def on(self, event):
                pass

        @saga(name="async-s", source="order", target="inventory", sync=False)
        class AsyncS:
            @handles(str)
            def on(self, event):
                pass

        router = (
            Router("sagas")
            .with_handler(SyncS, SyncS)
            .with_handler(AsyncS, AsyncS)
            .build()
        )
        assert router.sync_output_domains() == ["inventory"]
        assert router.has_async_outputs() is True

    def test_pm_router_with_mixed_handlers_same_target(self) -> None:
        from dataclasses import dataclass

        from angzarr_client.router import Router, handles, process_manager

        @dataclass
        class State:
            ok: bool = False

        @process_manager(
            name="sync-pm",
            pm_domain="fulfillment",
            sources=["order"],
            targets=["inventory"],
            sync_targets=["inventory"],
            state=State,
        )
        class SyncPM:
            @handles(int)
            def on(self, event, state):
                pass

        @process_manager(
            name="async-pm",
            pm_domain="fulfillment",
            sources=["order"],
            targets=["inventory"],
            sync_targets=[],
            state=State,
        )
        class AsyncPM:
            @handles(str)
            def on(self, event, state):
                pass

        router = (
            Router("pms")
            .with_handler(SyncPM, SyncPM)
            .with_handler(AsyncPM, AsyncPM)
            .build()
        )
        # Both handlers emit to the same target; the deduped sets cover
        # only "inventory" and would falsely report no async outputs.
        # The per-handler check correctly reports True.
        assert router.sync_output_domains() == ["inventory"]
        assert router.output_domains() == ["inventory"]
        assert router.has_async_outputs() is True

    def test_base_router_default_is_false(self) -> None:
        # CH / Projector / Upcaster routers inherit the base
        # `_BuiltRouterBase.has_async_outputs()` which always returns
        # False — they never emit cross-domain commands.
        from angzarr_client.router.runtime import _BuiltRouterBase

        class _Stub(_BuiltRouterBase):
            pass

        assert _Stub([]).has_async_outputs() is False
