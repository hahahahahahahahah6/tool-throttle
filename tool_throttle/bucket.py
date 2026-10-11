"""Thread-safe token bucket used by tool-throttle.

The bucket is deliberately simple: a fixed ``capacity`` (burst size) and a
steady ``refill_per_second`` rate. A ``clock`` callable can be injected so
tests can control time deterministically.
"""

from __future__ import annotations

import threading
import time


class TokenBucket:
    """A classic token bucket.

    * ``capacity`` -- maximum burst size (tokens).
    * ``refill_per_second`` -- steady refill rate (tokens per second).
    """

    def __init__(self, capacity: float, refill_per_second: float,
                 clock=time.monotonic) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be > 0")
        if refill_per_second <= 0:
            raise ValueError("refill_per_second must be > 0")
        self.capacity = float(capacity)
        self.refill_per_second = float(refill_per_second)
        self._clock = clock
        self._lock = threading.Lock()
        self._tokens = float(capacity)
        self._last = float(clock())

    def _refill(self, now: float) -> None:
        elapsed = now - self._last
        if elapsed > 0:
            self._tokens = min(self.capacity,
                               self._tokens + elapsed * self.refill_per_second)
            self._last = now

    def try_consume(self, n: float = 1.0) -> bool:
        """Consume ``n`` tokens if available; return True, else leave state
        untouched and return False."""
        with self._lock:
            self._refill(self._clock())
            if self._tokens >= n:
                self._tokens -= n
                return True
            return False

    def peek(self) -> float:
        """Current token count after applying refill (does not consume)."""
        with self._lock:
            self._refill(self._clock())
            return self._tokens

    def deficit_wait(self, n: float = 1.0) -> float:
        """Seconds until ``n`` tokens are available (>= 0)."""
        with self._lock:
            self._refill(self._clock())
            if self._tokens >= n:
                return 0.0
            return (n - self._tokens) / self.refill_per_second

    def reset(self) -> None:
        """Refill the bucket to full capacity."""
        with self._lock:
            self._tokens = self.capacity
            self._last = float(self._clock())
