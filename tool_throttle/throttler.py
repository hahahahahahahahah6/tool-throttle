"""The MCP tool-call throttle: intercept tool calls, apply token-bucket
quotas per tool name, and either queue or reject over-quota calls.

This is the semantic-layer "fuse" for agents: it does not care about HTTP
or API keys, it cares about *tool call intent* -- e.g. an agent stuck in a
loop hammering ``web_search`` 400 times a minute.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from .bucket import TokenBucket
from .config import QUEUE, REJECT, Quota, ThrottlerConfig


class ThrottledError(Exception):
    """Raised when a tool call exceeds its quota.

    ``retry_after`` is seconds until the call would be allowed (0 if the
    caller should not retry, e.g. queue-wait exhausted).
    """

    def __init__(self, tool: str, retry_after: float, reason: str) -> None:
        super().__init__(f"tool call throttled: {tool} ({reason})")
        self.tool = tool
        self.retry_after = float(retry_after)
        self.reason = reason


@dataclass
class Decision:
    """The outcome of a throttling check for one tool call."""
    tool: str
    allowed: bool
    wait_seconds: float       # 0 if allowed immediately
    reason: str               # "ok" | "quota_exceeded"
    pattern: str | None = None  # which per_tool pattern matched (if any)


class Throttler:
    """Intercept and throttle tool calls by tool name.

    Each distinct tool name gets its own :class:`TokenBucket` sized by the
    first matching quota in ``config.per_tool`` (or ``config.default``).
    """

    def __init__(self, config: ThrottlerConfig | None = None,
                 clock=time.monotonic) -> None:
        self.config = config or ThrottlerConfig()
        self._clock = clock
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._buckets: dict[str, tuple[TokenBucket, Quota, str | None]] = {}
        self._stats: dict[str, dict[str, float]] = {}

    # -- internals ------------------------------------------------------

    def _entry(self, tool: str) -> tuple[TokenBucket, Quota, str | None]:
        entry = self._buckets.get(tool)
        if entry is None:
            quota, pattern = self.config.quota_for(tool)
            entry = (TokenBucket(quota.capacity, quota.refill_per_second,
                                 clock=self._clock), quota, pattern)
            self._buckets[tool] = entry
            self._stats[tool] = {"allowed": 0, "rejected": 0, "queued": 0,
                                 "waited_seconds": 0.0,
                                 "quota": f"{quota.calls}/{quota.per_seconds}s",
                                 "pattern": pattern or "default"}
        return entry

    # -- public API -----------------------------------------------------

    def decide(self, tool: str, n: float = 1.0) -> Decision:
        """Check whether a call would be allowed *without* consuming tokens."""
        with self._lock:
            bucket, _, pattern = self._entry(tool)
        wait = bucket.deficit_wait(n)
        if wait <= 0:
            return Decision(tool, True, 0.0, "ok", pattern)
        return Decision(tool, False, wait, "quota_exceeded", pattern)

    def acquire(self, tool: str, n: float = 1.0) -> Decision:
        """Consume a token for ``tool``.

        With ``on_exceed="queue"`` this blocks (up to ``max_queue_wait``
        seconds) until a token is available, then proceeds. With
        ``on_exceed="reject"`` -- or when the wait would exceed the bound --
        raises :class:`ThrottledError`.
        """
        with self._lock:
            bucket, quota, pattern = self._entry(tool)
            stats = self._stats[tool]
        if bucket.try_consume(n):
            with self._lock:
                stats["allowed"] += 1
            return Decision(tool, True, 0.0, "ok", pattern)

        wait_needed = bucket.deficit_wait(n)
        remaining = self.config.max_queue_wait
        if self.config.on_exceed == REJECT or wait_needed > remaining:
            with self._lock:
                stats["rejected"] += 1
            raise ThrottledError(tool, wait_needed, "quota_exceeded")

        # queue: wait in small slices until a token frees up or we time out
        queued = True
        start = self._clock()
        deadline = start + remaining
        while True:
            sleep_for = min(wait_needed, 0.05)
            with self._cond:
                self._cond.wait(timeout=sleep_for)
            if bucket.try_consume(n):
                waited = max(0.0, self._clock() - start)
                with self._lock:
                    stats["allowed"] += 1
                    if queued:
                        stats["queued"] += 1
                        stats["waited_seconds"] += waited
                return Decision(tool, True, waited, "queued", pattern)
            wait_needed = bucket.deficit_wait(n)
            remaining = deadline - self._clock()
            if remaining <= 0:
                with self._lock:
                    stats["rejected"] += 1
                raise ThrottledError(tool, wait_needed, "queue_wait_exceeded")

    def wrap(self, tool: str, fn):
        """Return a wrapper that throttles calls to ``fn`` as ``tool``."""
        def _wrapped(*args, **kwargs):
            self.acquire(tool)
            return fn(*args, **kwargs)
        _wrapped.__name__ = getattr(fn, "__name__", f"throttled_{tool}")
        _wrapped.__doc__ = getattr(fn, "__doc__", None)
        return _wrapped

    def call(self, tool: str, fn, *args, **kwargs):
        """Call ``fn(*args, **kwargs)`` through the throttle as ``tool``."""
        self.acquire(tool)
        return fn(*args, **kwargs)

    def stats(self) -> dict[str, dict[str, float]]:
        """Per-tool counters: allowed / rejected / queued / waited_seconds."""
        with self._lock:
            return {t: dict(s) for t, s in self._stats.items()}

    def reset(self, tool: str | None = None) -> None:
        """Refill bucket(s) to full. ``tool=None`` resets everything."""
        with self._lock:
            targets = [tool] if tool else list(self._buckets)
            for t in targets:
                if t in self._buckets:
                    self._buckets[t][0].reset()

    def clear(self) -> None:
        """Drop all buckets and stats (fresh state, same config)."""
        with self._lock:
            self._buckets.clear()
            self._stats.clear()
