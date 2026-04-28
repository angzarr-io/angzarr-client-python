"""Step defs for features/client/retry.feature."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from angzarr_client import CommandRejectedError
from angzarr_client.error_codes import codes, keys
from angzarr_client.retry import ExponentialBackoffRetry, default_retry_policy

scenarios("retry.feature")


@dataclass
class _State:
    policy: Any = None
    op: Any = None
    call_count: int = 0
    on_retry_calls: int = 0
    result: Any = None
    error: Any = None
    rejected: Any = None


@pytest.fixture
def state() -> _State:
    return _State()


@when("I obtain the default retry policy")
def _when_default_policy(state: _State) -> None:
    state.policy = default_retry_policy()


@then(parsers.parse("the policy has min_delay {ms:d} ms"))
def _then_min_delay(state: _State, ms: int) -> None:
    assert int(state.policy.min_delay * 1000) == ms


@then(parsers.parse("the policy has max_delay {ms:d} ms"))
def _then_max_delay(state: _State, ms: int) -> None:
    assert int(state.policy.max_delay * 1000) == ms


@then(parsers.parse("the policy has max_attempts {n:d}"))
def _then_max_attempts(state: _State, n: int) -> None:
    assert state.policy.max_attempts == n


@then("the policy has jitter enabled")
def _then_jitter_enabled(state: _State) -> None:
    assert state.policy.jitter is True


@given(
    parsers.parse(
        "an ExponentialBackoffRetry with max_attempts {n:d} and jitter disabled"
    )
)
def _given_policy(state: _State, n: int) -> None:
    state.policy = ExponentialBackoffRetry(
        min_delay=0.000001,  # effectively no sleep
        max_delay=0.000001,
        max_attempts=n,
        jitter=False,
    )


@given(
    parsers.parse("an operation that fails {fail_count:d} times then returns {value:d}")
)
def _given_op_fails_then(state: _State, fail_count: int, value: int) -> None:
    def op():
        state.call_count += 1
        if state.call_count <= fail_count:
            raise RuntimeError(f"fail-{state.call_count}")
        return value

    state.op = op


@given("an operation that always fails")
def _given_op_always_fails(state: _State) -> None:
    def op():
        state.call_count += 1
        raise RuntimeError(f"fail-{state.call_count}")

    state.op = op


@given("an on_retry callback that counts invocations")
def _given_on_retry(state: _State) -> None:
    def cb(_attempt, _exc):
        state.on_retry_calls += 1

    state.policy.on_retry = cb


@when("I execute the operation through the retry policy")
def _when_execute(state: _State) -> None:
    try:
        state.result = state.policy.execute(state.op)
    except Exception as e:
        state.error = e


@then(parsers.parse("the returned value is {value:d}"))
def _then_returned_value(state: _State, value: int) -> None:
    assert state.result == value


@then(parsers.parse("the operation was called {n:d} times"))
def _then_call_count(state: _State, n: int) -> None:
    assert state.call_count == n


@then("the result is an error")
def _then_result_is_error(state: _State) -> None:
    assert state.error is not None


@then(parsers.parse("the on_retry callback was invoked {n:d} times"))
def _then_on_retry_count(state: _State, n: int) -> None:
    assert state.on_retry_calls == n


@when(
    parsers.parse(
        'I construct a CommandRejectedError via precondition_failed with reason "{reason}"'
    )
)
def _when_precondition_failed(state: _State, reason: str) -> None:
    # Audit #59: static message + structured detail. The cucumber-supplied
    # `reason` rides as a detail value rather than being interpolated into
    # the message string.
    state.rejected = CommandRejectedError.precondition_failed(
        code=codes.STATUS_MISMATCH,
        message="precondition failed",
        details={keys.CONTEXT: reason},
    )


@then("the error's is_precondition_failed predicate is true")
def _then_pf_true(state: _State) -> None:
    assert state.rejected.is_precondition_failed() is True


@then("the error's is_invalid_argument predicate is false")
def _then_ia_false(state: _State) -> None:
    assert state.rejected.is_invalid_argument() is False


@then("the error's is_not_found predicate is false")
def _then_nf_false(state: _State) -> None:
    assert state.rejected.is_not_found() is False
