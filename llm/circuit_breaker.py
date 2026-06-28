"""
Circuit breaker for the Ollama backend.
CLOSED → (failures ≥ threshold) → OPEN → (after recovery_timeout) → HALF_OPEN → CLOSED|OPEN.

When OPEN, the caller (fusion/adjudicate.py) takes the keep-separate-and-flag path.
Fusion never blocks on the model.

Time is injected (a monotonic clock callable) so tests are deterministic and the
breaker never calls now() implicitly.
"""
from __future__ import annotations

from enum import Enum


class State(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class LLMUnavailable(RuntimeError):
    """Raised when the circuit is open or a call exhausts retries.

    fusion/adjudicate.py catches this and keeps the pair separate + flags it.
    """


class CircuitBreaker:
    def __init__(self, failure_threshold: int, recovery_timeout_s: float, clock=None):
        self._threshold = failure_threshold
        self._recovery = recovery_timeout_s
        self._failures = 0
        self._state = State.CLOSED
        self._opened_at = 0.0
        # injected monotonic clock; defaults to time.monotonic but never called in pure tests
        if clock is None:
            import time
            clock = time.monotonic
        self._clock = clock

    @property
    def state(self) -> State:
        return self._state

    def allow(self) -> bool:
        """Return True if a call may proceed; transition OPEN→HALF_OPEN when recovered."""
        if self._state == State.OPEN:
            if self._clock() - self._opened_at >= self._recovery:
                self._state = State.HALF_OPEN
                return True
            return False
        return True

    def record_success(self) -> None:
        self._failures = 0
        self._state = State.CLOSED

    def record_failure(self) -> None:
        self._failures += 1
        if self._state == State.HALF_OPEN or self._failures >= self._threshold:
            self._state = State.OPEN
            self._opened_at = self._clock()
