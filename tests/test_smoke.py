"""Smoke tests for tool-throttle. All must be green before shipping."""

import json
import os
import tempfile
import time

import pytest

from tool_throttle import (
    QUEUE, REJECT, Quota, ThrottledError, Throttler, ThrottlerConfig,
    TokenBucket,
)


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


# 1. tokens refill at the configured rate (deterministic, fake clock)
def test_refill_math():
    c = FakeClock()
    b = TokenBucket(capacity=5, refill_per_second=2.0, clock=c)
    assert b.try_consume(5) is True
    assert b.try_consume(1) is False
    c.advance(1.0)
    assert b.peek() == pytest.approx(2.0)
    assert b.try_consume(2) is True
    assert b.try_consume(1) is False


# 2. idle buckets never exceed capacity (burst cap)
def test_burst_cap():
    c = FakeClock()
    b = TokenBucket(capacity=3, refill_per_second=100.0, clock=c)
    b.try_consume(3)
    c.advance(3600)  # idle for an hour
    assert b.peek() == pytest.approx(3.0)


# 3. per-tool quotas override the default; unmatched tools use default
def test_per_tool_override():
    c = FakeClock()
    th = Throttler(ThrottlerConfig(
        default=Quota(calls=60, per_seconds=60),
        per_tool={"expensive_tool": Quota(calls=2, per_seconds=60)},
        on_exceed=REJECT), clock=c)
    th.acquire("expensive_tool")
    th.acquire("expensive_tool")
    with pytest.raises(ThrottledError):
        th.acquire("expensive_tool")
    # default tool is unaffected
    th.acquire("cheap_tool")
    d = th.decide("cheap_tool")
    assert d.pattern is None  # default has no pattern


# 4. first matching pattern wins (insertion order)
def test_first_match_wins():
    c = FakeClock()
    th = Throttler(ThrottlerConfig(
        per_tool={"web_*": Quota(calls=2, per_seconds=60),
                  "web_search": Quota(calls=50, per_seconds=60)},
        on_exceed=REJECT), clock=c)
    d = th.decide("web_search")
    assert d.pattern == "web_*"
    th.acquire("web_search")
    th.acquire("web_search")
    with pytest.raises(ThrottledError):
        th.acquire("web_search")


# 5. reject policy fails fast and reports retry_after
def test_reject_reports_retry_after():
    c = FakeClock()
    th = Throttler(ThrottlerConfig(
        default=Quota(calls=1, per_seconds=60), on_exceed=REJECT), clock=c)
    th.acquire("t")
    t0 = time.monotonic()
    with pytest.raises(ThrottledError) as ei:
        th.acquire("t")
    assert time.monotonic() - t0 < 0.5  # fast, no blocking
    assert ei.value.retry_after == pytest.approx(60.0, abs=1.0)
    assert ei.value.tool == "t"
    assert th.stats()["t"]["rejected"] == 1


# 6. queue policy waits for a token then proceeds (real clock)
def test_queue_waits_then_proceeds():
    th = Throttler(ThrottlerConfig(
        default=Quota(calls=2, per_seconds=1, burst=1),  # 1 token every 0.5s
        on_exceed=QUEUE, max_queue_wait=5.0))
    th.acquire("t")  # consumes the 1-token burst
    t0 = time.monotonic()
    d = th.acquire("t")
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.4  # actually waited for refill
    assert d.allowed and d.wait_seconds >= 0.4
    assert th.stats()["t"]["queued"] == 1


# 7. queue policy is bounded: a call needing an hour-long wait fails fast
def test_queue_bound_never_blocks():
    th = Throttler(ThrottlerConfig(
        default=Quota(calls=1, per_seconds=3600),  # ~1/hour refill
        on_exceed=QUEUE, max_queue_wait=0.2))
    th.acquire("t")
    t0 = time.monotonic()
    with pytest.raises(ThrottledError) as ei:
        th.acquire("t")
    elapsed = time.monotonic() - t0
    assert elapsed < 1.0  # bounded, never blocks for the full hour
    assert ei.value.reason == "quota_exceeded"
    assert ei.value.retry_after > 3000
    assert th.stats()["t"]["rejected"] == 1


# 8. wrap() passes return values through and enforces the quota
def test_wrap_integration():
    c = FakeClock()
    th = Throttler(ThrottlerConfig(
        default=Quota(calls=3, per_seconds=60), on_exceed=REJECT), clock=c)

    def add(a, b):
        """adds two numbers"""
        return a + b

    safe_add = th.wrap("math", add)
    assert safe_add(2, 3) == 5
    assert safe_add.__name__ == "add"
    safe_add(1, 1)
    safe_add(1, 1)
    with pytest.raises(ThrottledError):
        safe_add(1, 1)
    s = th.stats()["math"]
    assert s["allowed"] == 3 and s["rejected"] == 1


# 9. stats() exposes per-tool counters
def test_stats_shape():
    c = FakeClock()
    th = Throttler(ThrottlerConfig(
        default=Quota(calls=1, per_seconds=60),
        per_tool={"web_*": Quota(calls=5, per_seconds=60)},
        on_exceed=REJECT), clock=c)
    th.acquire("web_search")
    try:
        th.acquire("other")
        th.acquire("other")
    except ThrottledError:
        pass
    s = th.stats()
    assert set(s) == {"web_search", "other"}
    for tool, counters in s.items():
        assert set(counters) == {"allowed", "rejected", "queued",
                                 "waited_seconds", "quota", "pattern"}
    assert s["web_search"]["allowed"] == 1
    assert s["other"]["rejected"] == 1
    assert s["web_search"]["pattern"] == "web_*"


# 10. config survives a JSON round-trip
def test_config_json_roundtrip():
    cfg = ThrottlerConfig(
        default=Quota(calls=30, per_seconds=60),
        per_tool={"expensive_*": Quota(calls=2, per_seconds=60, burst=4)},
        on_exceed=REJECT, max_queue_wait=7.5)
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "throttle.json")
        cfg.save(p)
        loaded = ThrottlerConfig.load(p)
    assert loaded == cfg
    q, pat = loaded.quota_for("expensive_llm")
    assert (q.calls, q.per_seconds, q.burst, pat) == (2, 60.0, 4, "expensive_*")
    # round-tripped config behaves identically (burst=4 -> 4 tokens)
    c = FakeClock()
    th = Throttler(loaded, clock=c)
    for _ in range(4):
        th.acquire("expensive_llm")
    with pytest.raises(ThrottledError):
        th.acquire("expensive_llm")
