"""Tests for angzarr_client.retry."""

from __future__ import annotations

import pytest

from angzarr_client.retry import ExponentialBackoffRetry, default_retry_policy


class TestExponentialBackoffRetry:
    def test_returns_result_when_operation_succeeds(self, monkeypatch) -> None:
        monkeypatch.setattr("time.sleep", lambda _d: None)
        policy = ExponentialBackoffRetry(min_delay=0.0, max_delay=0.0, max_attempts=3)
        result = policy.execute(lambda: 42)
        assert result == 42

    def test_retries_until_success(self, monkeypatch) -> None:
        monkeypatch.setattr("time.sleep", lambda _d: None)
        calls = {"n": 0}

        def op():
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("temp")
            return "ok"

        policy = ExponentialBackoffRetry(
            min_delay=0.0, max_delay=0.0, max_attempts=5, jitter=False
        )
        assert policy.execute(op) == "ok"
        assert calls["n"] == 3

    def test_raises_last_error_after_exhaustion(self, monkeypatch) -> None:
        monkeypatch.setattr("time.sleep", lambda _d: None)

        errors = [RuntimeError(f"attempt {i}") for i in range(3)]
        calls = {"n": 0}

        def op():
            err = errors[calls["n"]]
            calls["n"] += 1
            raise err

        policy = ExponentialBackoffRetry(
            min_delay=0.0, max_delay=0.0, max_attempts=3, jitter=False
        )
        with pytest.raises(RuntimeError, match="attempt 2"):
            policy.execute(op)

    def test_on_retry_invoked_between_attempts(self, monkeypatch) -> None:
        monkeypatch.setattr("time.sleep", lambda _d: None)
        observed: list[tuple[int, str]] = []

        def on_retry(attempt: int, err: Exception) -> None:
            observed.append((attempt, str(err)))

        calls = {"n": 0}

        def op():
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError(f"boom-{calls['n']}")
            return "done"

        policy = ExponentialBackoffRetry(
            min_delay=0.0,
            max_delay=0.0,
            max_attempts=5,
            jitter=False,
            on_retry=on_retry,
        )
        policy.execute(op)
        assert observed == [(0, "boom-1"), (1, "boom-2")]

    def test_compute_delay_capped_by_max(self) -> None:
        policy = ExponentialBackoffRetry(
            min_delay=0.1, max_delay=0.2, max_attempts=10, jitter=False
        )
        # attempt 0 → 0.1; attempt 1 → 0.2; attempt 5 → 3.2 but capped to 0.2
        assert policy._compute_delay(0) == pytest.approx(0.1)
        assert policy._compute_delay(5) == pytest.approx(0.2)

    def test_compute_delay_jitter_bounds(self) -> None:
        policy = ExponentialBackoffRetry(
            min_delay=0.1, max_delay=1.0, max_attempts=10, jitter=True
        )
        for _ in range(20):
            d = policy._compute_delay(1)  # base = 0.2
            assert 0.5 * 0.2 <= d <= 0.2


class TestDefaultRetryPolicy:
    def test_default_policy_is_exponential(self) -> None:
        p = default_retry_policy()
        assert isinstance(p, ExponentialBackoffRetry)
        assert p.min_delay == 0.1
        assert p.max_delay == 5.0
        assert p.max_attempts == 10
        assert p.jitter is True
