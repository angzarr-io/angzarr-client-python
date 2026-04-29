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
from dataclasses import dataclass
from typing import Union

DEFAULT_PROBE_INTERVAL = 30.0
"""Default cadence for re-evaluating output-domain probes (seconds)."""

DEFAULT_PROBE_TIMEOUT = 2.0
"""Default per-probe timeout (seconds)."""

ENV_INTERVAL = "ANGZARR_READINESS_PROBE_INTERVAL"
ENV_TIMEOUT = "ANGZARR_READINESS_PROBE_TIMEOUT"
ENV_BUS_ENDPOINT = "ANGZARR_BUS_ENDPOINT"
"""Audit #74: optional async-bus endpoint (Kafka / RabbitMQ / SQS /
SNS / NATS / etc.). When set, a single :class:`BusProbe` covers
reachability of the async path for every async-only saga / PM target.
When unset, no bus probe is added — async-only targets are simply not
part of readiness."""

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
    def new(cls) -> tuple["TransportProbe", TransportSignal]:
        signal = TransportSignal()
        return cls(signal), signal

    @property
    def name(self) -> str:
        return "transport"

    async def check(self) -> bool:
        return self._signal.is_bound()


@dataclass(frozen=True)
class _TcpEndpoint:
    addr: str  # "host:port"


@dataclass(frozen=True)
class _UdsEndpoint:
    path: str


_Endpoint = Union[_TcpEndpoint, _UdsEndpoint]


def _parse_endpoint(raw: str) -> _Endpoint:
    """Classify a resolved endpoint string into TCP or UDS. ``unix:``
    prefix or leading ``/`` selects UDS; otherwise TCP.
    """
    if raw.startswith("unix:"):
        return _UdsEndpoint(raw[len("unix:") :])
    if raw.startswith("/"):
        return _UdsEndpoint(raw)
    return _TcpEndpoint(raw)


async def _try_tcp_connect(addr: str) -> bool:
    host, sep, port_s = addr.rpartition(":")
    if not sep or not port_s:
        return False
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    try:
        port = int(port_s)
    except ValueError:
        return False
    try:
        _, writer = await asyncio.open_connection(host or "localhost", port)
    except OSError:
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except OSError:
        pass
    return True


async def _try_uds_connect(path: str) -> bool:
    try:
        _, writer = await asyncio.open_unix_connection(path)
    except (OSError, FileNotFoundError):
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except OSError:
        pass
    return True


class OutputDomainProbe(Probe):
    """Per-output-domain coordinator probe — attempts to open a connection
    to the downstream domain's command-handler coordinator endpoint.

    Audit #74: built only for **sync** output domains (declared via
    ``@saga(sync=True)`` or ``@process_manager(sync_targets=[...])``).
    Async-only targets ride the bus; see :class:`BusProbe`.
    """

    def __init__(self, domain: str, endpoint: _Endpoint) -> None:
        self._domain = domain
        self._endpoint = endpoint

    @classmethod
    def for_domain(cls, domain: str) -> "OutputDomainProbe":
        from .client import resolve_ch_endpoint

        raw = resolve_ch_endpoint(domain)
        return cls(domain, _parse_endpoint(raw))

    @property
    def name(self) -> str:
        return self._domain

    async def check(self) -> bool:
        if isinstance(self._endpoint, _TcpEndpoint):
            return await _try_tcp_connect(self._endpoint.addr)
        return await _try_uds_connect(self._endpoint.path)


class BusProbe(Probe):
    """Async-bus reachability probe (audit #74) — covers the path that
    async-only saga / PM targets ride. The endpoint is operator-supplied
    via :data:`ENV_BUS_ENDPOINT` and points at whatever broker the
    deployment uses (Kafka, RabbitMQ, SQS/SNS, NATS, etc.).

    Connection-only — confirms the broker is reachable, not that
    publishes will succeed end-to-end. Same contract as
    :class:`OutputDomainProbe` for sync targets.
    """

    def __init__(self, endpoint: _Endpoint) -> None:
        self._endpoint = endpoint

    @classmethod
    def from_env(cls) -> "BusProbe | None":
        raw = os.environ.get(ENV_BUS_ENDPOINT)
        if not raw:
            return None
        return cls(_parse_endpoint(raw))

    @property
    def name(self) -> str:
        return "bus"

    async def check(self) -> bool:
        if isinstance(self._endpoint, _TcpEndpoint):
            return await _try_tcp_connect(self._endpoint.addr)
        return await _try_uds_connect(self._endpoint.path)


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
                _LOG.warning(
                    "readiness probe timed out", extra={"probe": probe.name}
                )
                ok = False
            except Exception:  # noqa: BLE001 — supervisor is hard-isolated
                _LOG.exception("readiness probe %r raised", probe.name)
                ok = False
            else:
                if not ok:
                    _LOG.warning(
                        "readiness probe failed", extra={"probe": probe.name}
                    )
            if not ok:
                all_ok = False
        status = SERVING if all_ok else NOT_SERVING
        for name in service_names:
            await health_servicer.set(name, status)
        await asyncio.sleep(interval)
