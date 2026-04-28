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

Sync/async note: Python's ``grpcio`` server is synchronous (runs on a
``ThreadPoolExecutor``), so the supervisor runs in a daemon thread
rather than an asyncio task. Probes have ``check() -> bool``
synchronous signatures; per-probe timeouts are enforced via socket
timeouts. Mirrors Rust's tokio-async supervisor at the wire-behavior
level, not the language-runtime level.
"""

from __future__ import annotations

import os
import socket
import threading
from abc import ABC, abstractmethod

from grpc_health.v1 import health, health_pb2

from .client import resolve_ch_endpoint

# Default cadence for re-evaluating output-domain probes (seconds).
DEFAULT_PROBE_INTERVAL = 30.0
# Default per-probe timeout (seconds).
DEFAULT_PROBE_TIMEOUT = 2.0

ENV_INTERVAL = "ANGZARR_READINESS_PROBE_INTERVAL"
ENV_TIMEOUT = "ANGZARR_READINESS_PROBE_TIMEOUT"


def probe_config_from_env() -> tuple[float, float]:
    """Read supervisor cadence + per-probe timeout from env, falling
    back to :data:`DEFAULT_PROBE_INTERVAL` / :data:`DEFAULT_PROBE_TIMEOUT`.

    Same env-var contract as Rust (``ANGZARR_READINESS_PROBE_INTERVAL``
    + ``_TIMEOUT``, integer seconds). Returns ``(interval, timeout)``
    in seconds (float). Bad values (non-numeric) silently fall back to
    defaults — matches Rust's ``.parse::<u64>().ok()`` behavior.
    """

    def _read(env_var: str, default: float) -> float:
        raw = os.environ.get(env_var)
        if raw is None:
            return default
        try:
            return float(raw)
        except ValueError:
            return default

    return (
        _read(ENV_INTERVAL, DEFAULT_PROBE_INTERVAL),
        _read(ENV_TIMEOUT, DEFAULT_PROBE_TIMEOUT),
    )


class Probe(ABC):
    """Single readiness probe — evaluated once per supervisor tick.

    Subclasses implement ``name`` (stable identifier for log lines)
    and ``check`` (returns ``True`` when the underlying dependency is
    healthy). ``check`` is called in the supervisor thread; per-probe
    timeouts are enforced by the supervisor via socket-level timeouts
    (subclasses don't need their own timeout handling unless they do
    non-socket work).
    """

    @abstractmethod
    def name(self) -> str:
        """Stable identifier for log lines and per-probe service names."""

    @abstractmethod
    def check(self, timeout: float) -> bool:
        """Return ``True`` if the underlying dependency is currently healthy.

        Args:
            timeout: Wall-clock seconds the probe is allowed to take.
                Subclasses should use this as an upper bound on any
                blocking I/O they do (e.g. ``socket.settimeout``).
        """


class TransportSignal:
    """Side of the :class:`TransportProbe` used by the runner to mark
    "bound and serving"."""

    def __init__(self, event: threading.Event) -> None:
        self._event = event

    def mark_bound(self) -> None:
        """Mark the transport as accepting traffic. From this point the
        sibling :class:`TransportProbe` reports ``True``."""
        self._event.set()


class TransportProbe(Probe):
    """One-shot transport probe — flipped ``True`` once the listener has
    bound and the server is accepting traffic. From that point its
    result never changes.

    Mirrors Rust's :rust:`TransportProbe` /
    :rust:`TransportSignal::mark_bound()` pair. Use
    :meth:`new` to construct both halves."""

    def __init__(self, event: threading.Event) -> None:
        self._event = event

    @classmethod
    def new(cls) -> tuple[TransportProbe, TransportSignal]:
        """Build the probe + signal pair.

        The probe is registered with the supervisor; the signal is held
        by the runner and flipped once the gRPC server has bound its
        listener and called ``server.start()``.
        """
        ev = threading.Event()
        return (cls(ev), TransportSignal(ev))

    def name(self) -> str:
        return "transport"

    def check(self, timeout: float) -> bool:
        # No-op timeout — flag flip is in-process, doesn't block.
        return self._event.is_set()


class OutputDomainProbe(Probe):
    """Per-output-domain coordinator probe — attempts to open a
    connection to the downstream domain's command handler coordinator
    endpoint.

    Resolves the endpoint at construction time via
    :func:`resolve_ch_endpoint(domain)`; failures of the env config
    surface here (not at first probe tick) — matches Rust's startup-time
    resolution.
    """

    def __init__(self, domain: str) -> None:
        self._domain = domain
        raw = resolve_ch_endpoint(domain)
        # Strip an optional unix: prefix so the connect path can branch
        # on raw filesystem path vs host:port. resolve_ch_endpoint
        # returns either ``host:port`` (distributed) or
        # ``{base}/ch-{domain}.sock`` (standalone, no prefix).
        if raw.startswith("unix:"):
            self._uds_path: str | None = raw[len("unix:") :]
        elif raw.startswith("/"):
            self._uds_path = raw
        else:
            self._uds_path = None
        self._tcp_addr: str | None = None if self._uds_path else raw

    @classmethod
    def for_domain(cls, domain: str) -> OutputDomainProbe:
        return cls(domain)

    def name(self) -> str:
        return self._domain

    def check(self, timeout: float) -> bool:
        if self._uds_path is not None:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            try:
                sock.connect(self._uds_path)
                return True
            except OSError:
                return False
            finally:
                sock.close()

        # TCP host:port.
        host, _, port_str = (self._tcp_addr or "").rpartition(":")
        if not host or not port_str:
            return False
        try:
            port = int(port_str)
        except ValueError:
            return False
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False


class _SupervisorThread(threading.Thread):
    """Daemon thread running the readiness supervisor loop.

    Held internally by :func:`run_supervisor` so callers can ``stop()``
    on shutdown. Mirrors Rust's ``tokio::spawn(run_supervisor(...))``
    + ``supervisor.abort()`` lifecycle.
    """

    def __init__(
        self,
        probes: list[Probe],
        health_servicer: health.HealthServicer,
        service_names: list[str],
        interval: float,
        timeout: float,
    ) -> None:
        super().__init__(name="angzarr-readiness", daemon=True)
        self._probes = probes
        self._health = health_servicer
        self._service_names = service_names
        self._interval = interval
        self._timeout = timeout
        self._stop = threading.Event()

    def stop(self) -> None:
        """Signal the supervisor to exit on its next loop iteration."""
        self._stop.set()

    def run(self) -> None:
        while not self._stop.is_set():
            all_ok = True
            for probe in self._probes:
                try:
                    ok = probe.check(self._timeout)
                except Exception:  # noqa: BLE001 — broad on purpose; probe
                    # failures must not crash the supervisor thread.
                    ok = False
                if not ok:
                    all_ok = False
            status = (
                health_pb2.HealthCheckResponse.SERVING
                if all_ok
                else health_pb2.HealthCheckResponse.NOT_SERVING
            )
            for name in self._service_names:
                self._health.set(name, status)
            # Sleep in small chunks so stop() is responsive on shutdown.
            self._stop.wait(timeout=self._interval)


def run_supervisor(
    probes: list[Probe],
    health_servicer: health.HealthServicer,
    service_names: list[str],
    interval: float,
    timeout: float,
) -> _SupervisorThread:
    """Start the readiness supervisor.

    Spawns a daemon thread that polls every probe on each tick,
    aggregates ``all_ok``, and publishes ``SERVING`` / ``NOT_SERVING``
    on every health-service name registered with ``health_servicer``.

    Returns the thread so the caller can ``.stop()`` it on shutdown.
    The thread is a daemon, so process exit also terminates it.

    Args:
        probes: List of :class:`Probe` instances to evaluate.
        health_servicer: The gRPC health servicer registered with the
            server. The supervisor calls ``set(name, status)`` per
            service name on each tick.
        service_names: Names to publish status under (typically
            ``["", "<kind-specific-service-name>"]`` — empty string is
            the overall server status, the kind-specific name is what
            ``Health.Check(service=...)`` matches).
        interval: Seconds between supervisor ticks.
        timeout: Per-probe timeout in seconds (passed to each
            ``Probe.check``).
    """
    thread = _SupervisorThread(
        probes,
        health_servicer,
        service_names,
        interval,
        timeout,
    )
    thread.start()
    return thread
