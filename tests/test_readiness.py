"""Tests for the readiness supervisor and probes.

Mirrors ``client-rust/main/src/readiness.rs`` semantics: TransportProbe
flips once the listener is bound (and never reverts);
``run_supervisor`` aggregates probes and publishes ``SERVING`` /
``NOT_SERVING`` to the named services on every tick.
"""

from __future__ import annotations

import asyncio

from grpc_health.v1 import health_pb2

from angzarr_client.readiness import (
    DEFAULT_PROBE_INTERVAL,
    DEFAULT_PROBE_TIMEOUT,
    Probe,
    TransportProbe,
    TransportSignal,
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

    # Audit #82: raising probes don't kill the supervisor; each cause
    # (timeout / raise / probe-False) emits a distinct log line.

    async def test_survives_raising_probe_and_publishes_not_serving(
        self, caplog
    ) -> None:
        class _Raises(Probe):
            def __init__(self) -> None:
                self.calls = 0

            @property
            def name(self) -> str:
                return "raiser"

            async def check(self) -> bool:
                self.calls += 1
                raise RuntimeError("boom")

        raiser = _Raises()
        health = _RecordingHealth()
        with caplog.at_level("WARNING"):
            task = asyncio.create_task(
                run_supervisor(
                    probes=[raiser],
                    health_servicer=health,
                    service_names=[""],
                    interval=0.01,
                    timeout=1.0,
                )
            )
            await asyncio.sleep(0.06)
            assert not task.done(), "supervisor exited early — exception leaked"
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Probe was called multiple times — the supervisor stayed alive.
        assert raiser.calls >= 2
        assert all(status == NOT_SERVING for _, status in health.calls)
        # Audit #90: panic logged at WARN (not ERROR) with structured
        # `probe` + `error` fields rather than a traceback. Mirrors
        # Rust's `warn!(probe, error=%msg, "readiness probe panicked")`.
        panic_records = [
            r
            for r in caplog.records
            if "panicked" in r.getMessage()
            and r.levelname == "WARNING"
            and getattr(r, "probe", None) == "raiser"
            and getattr(r, "error", None)
        ]
        assert panic_records, (
            "expected a WARN 'readiness probe panicked' log with structured "
            "probe + error fields"
        )

    async def test_logs_distinct_warning_on_timeout(self, caplog) -> None:
        slow = _SlowProbe("slow", delay=1.0)
        health = _RecordingHealth()
        with caplog.at_level("WARNING"):
            task = asyncio.create_task(
                run_supervisor(
                    probes=[slow],
                    health_servicer=health,
                    service_names=[""],
                    interval=0.05,
                    timeout=0.01,
                )
            )
            await asyncio.sleep(0.07)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert any(
            "timed out" in r.getMessage() for r in caplog.records
        ), "expected a 'readiness probe timed out' log"
        # No "readiness probe failed" log on the timeout path — the cause
        # warning is sufficient (audit #82 design call).
        assert not any(
            "readiness probe failed" in r.getMessage() for r in caplog.records
        )

    async def test_logs_distinct_warning_on_probe_false(self, caplog) -> None:
        probe = _FakeProbe("p", [False, False, False, False])
        health = _RecordingHealth()
        with caplog.at_level("WARNING"):
            task = asyncio.create_task(
                run_supervisor(
                    probes=[probe],
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

        # Probe-returned-False path produces "readiness probe failed", NOT
        # "timed out" / "raised".
        assert any("readiness probe failed" in r.getMessage() for r in caplog.records)
        assert not any("timed out" in r.getMessage() for r in caplog.records)


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
