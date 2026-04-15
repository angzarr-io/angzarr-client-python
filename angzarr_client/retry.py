"""Retry policy interface and default exponential backoff implementation."""

import math
import random
import time
from abc import ABC, abstractmethod
from typing import Callable, TypeVar

T = TypeVar("T")


class RetryPolicy(ABC):
    """Strategy for retrying failed operations."""

    @abstractmethod
    def execute(self, operation: Callable[[], T]) -> T:
        """Run the operation, retrying on failure according to the policy.

        Returns the result of the first successful attempt.
        Raises the last exception if all attempts fail.
        """
        ...


class ExponentialBackoffRetry(RetryPolicy):
    """Retries with exponential backoff and optional jitter."""

    def __init__(
        self,
        min_delay: float = 0.1,
        max_delay: float = 5.0,
        max_attempts: int = 10,
        jitter: bool = True,
    ):
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.max_attempts = max_attempts
        self.jitter = jitter

    def execute(self, operation: Callable[[], T]) -> T:
        last_err: Exception | None = None
        for attempt in range(self.max_attempts):
            try:
                return operation()
            except Exception as e:
                last_err = e
                if attempt < self.max_attempts - 1:
                    delay = self._compute_delay(attempt)
                    time.sleep(delay)
        raise last_err  # type: ignore[misc]

    def _compute_delay(self, attempt: int) -> float:
        delay = self.min_delay * math.pow(2, attempt)
        delay = min(delay, self.max_delay)
        if self.jitter:
            delay = delay * (0.5 + random.random() * 0.5)
        return delay


def default_retry_policy() -> RetryPolicy:
    """Return the standard retry policy matching Rust's backoff config."""
    return ExponentialBackoffRetry(
        min_delay=0.1,
        max_delay=5.0,
        max_attempts=10,
        jitter=True,
    )
