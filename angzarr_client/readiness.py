"""Readiness probes and health-status supervisor for runner servers.

Python port of ``client-rust/main/src/readiness.rs``. Audit #68.

A runner exposes its readiness through ``grpc.health.v1.Health``. While
any probe is failing, the per-kind service name reports ``NOT_SERVING``;
once every probe is green, it flips to ``SERVING``. Probes are evaluated
on a fixed cadence (default 30s, override via
``ANGZARR_READINESS_PROBE_INTERVAL``) with a per-probe timeout (default
2s, override via ``ANGZARR_READINESS_PROBE_TIMEOUT``).

Aggregation is binary — ``all up`` is ``SERVING``, anything else is
``NOT_SERVING``. The health server itself always responds, so liveness
("the process answers") and readiness ("it's safe to send traffic")
share one wire surface and are distinguished by the response status.

Async architecture (audit #68 design call): the runner runs on
``grpc.aio.server`` so the supervisor lives as an asyncio task on the
same event loop, mirroring Rust's tokio-async supervisor 1:1. Probes
have ``async check() -> bool`` signatures; per-probe timeouts are
enforced via ``asyncio.wait_for``.
"""

from __future__ import annotations

import abc
import asyncio
import logging
import os

DEFAULT_PROBE_INTERVAL = 30.0
"""Default cadence for re-evaluating probes (seconds)."""

DEFAULT_PROBE_TIMEOUT = 2.0
"""Default per-probe timeout (seconds)."""

ENV_INTERVAL = "ANGZARR_READINESS_PROBE_INTERVAL"
ENV_TIMEOUT = "ANGZARR_READINESS_PROBE_TIMEOUT"

_LOG = logging.getLogger(__name__)


def probe_config_from_env() -> tuple[float, float]:
    """Read supervisor cadence + per-probe timeout from env, falling
    back to :data:`DEFAULT_PROBE_INTERVAL` / :data:`DEFAULT_PROBE_TIMEOUT`.

    Same env-var contract as Rust. Bad values (non-numeric) silently
    fall back to defaults — matches Rust's ``.parse::<u64>().ok()``.
    """

    def _read(env_var: str, default: float) -> float:
        raw = os.environ.get(env_var)
        if raw is None or raw == "":
            return default
        try:
            return float(raw)
        except ValueError:
            return default

    return (
        _read(ENV_INTERVAL, DEFAULT_PROBE_INTERVAL),
        _read(ENV_TIMEOUT, DEFAULT_PROBE_TIMEOUT),
    )


class Probe(abc.ABC):
    """Single readiness probe — evaluated once per supervisor tick."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Stable identifier for log lines."""

    @abc.abstractmethod
    async def check(self) -> bool:
        """``True`` when the underlying dependency is currently healthy."""


class TransportSignal:
    """Side of a :class:`TransportProbe` used by the runner to mark
    'bound and serving'.
    """

    def __init__(self) -> None:
        self._bound = False

    def mark_bound(self) -> None:
        self._bound = True

    def is_bound(self) -> bool:
        return self._bound


class TransportProbe(Probe):
    """One-shot transport probe — flipped ``True`` once the listener has
    bound and the server is accepting traffic. From that point its
    result never changes.
    """

    def __init__(self, signal: TransportSignal) -> None:
        self._signal = signal

    @classmethod
    def new(cls) -> tuple[TransportProbe, TransportSignal]:
        signal = TransportSignal()
        return cls(signal), signal

    @property
    def name(self) -> str:
        return "transport"

    async def check(self) -> bool:
        return self._signal.is_bound()


async def run_supervisor(
    probes: list[Probe],
    health_servicer,
    service_names: list[str],
    interval: float,
    timeout: float,
) -> None:
    """Poll every probe on each tick, aggregate (`all_ok` → ``SERVING``,
    else ``NOT_SERVING``), publish to every service name. Loops until
    cancelled.

    ``health_servicer`` is an async ``grpc_health.v1.health.aio.HealthServicer``
    — its ``set(service, status)`` is awaitable.
    """
    from grpc_health.v1 import health_pb2

    SERVING = health_pb2.HealthCheckResponse.SERVING
    NOT_SERVING = health_pb2.HealthCheckResponse.NOT_SERVING

    while True:
        all_ok = True
        for probe in probes:
            # Audit #82: each cause (timeout / probe-raised / probe-returned-
            # False) emits its own warning; there is no aggregate "failed"
            # log on top — the cause warning is sufficient. Exceptions are
            # caught so a misbehaving probe can never kill the supervisor.
            try:
                ok = await asyncio.wait_for(probe.check(), timeout=timeout)
            except asyncio.TimeoutError:
                _LOG.warning("readiness probe timed out", extra={"probe": probe.name})
                ok = False
            except Exception as exc:  # noqa: BLE001 — supervisor is hard-isolated
                # Audit #90: align with Rust `warn!(probe, error=%msg,
                # "readiness probe panicked")`. WARN level matches Rust;
                # `_LOG.exception` would have logged at ERROR with a
                # full traceback, asymmetric on alert thresholds.
                _LOG.warning(
                    "readiness probe panicked",
                    extra={"probe": probe.name, "error": str(exc)},
                )
                ok = False
            else:
                if not ok:
                    _LOG.warning("readiness probe failed", extra={"probe": probe.name})
            if not ok:
                all_ok = False
        status = SERVING if all_ok else NOT_SERVING
        for name in service_names:
            await health_servicer.set(name, status)
        await asyncio.sleep(interval)
